// AI-hint: ratatui live dashboard for mios-fanctl: daemon/thermal state, per-header role/mode/duty/RPM, temperatures; read-only, refreshes every client.refresh_ms.
// AI-related: tools/native/mios-fanctl/src/view.rs, tools/native/mios-fanctl/src/api.rs
// AI-functions: run, event_loop, Snapshot::fetch, draw

use crate::{api::Client, config::Config, view};
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Cell, Paragraph, Row, Table};
use ratatui::{DefaultTerminal, Frame};
use serde_json::Value;
use std::time::{Duration, Instant};

struct Snapshot {
    status: Result<Value, String>,
    active: Result<Value, String>,
    fans: Result<Value, String>,
    headers: Result<Value, String>,
    sensors: Result<Value, String>,
}

impl Snapshot {
    fn fetch(c: &Client) -> Self {
        Self {
            status: c.get("/status"),
            active: c.get("/profile/active"),
            fans: c.get("/fans"),
            headers: c.get("/hwmon/headers"),
            sensors: c.get("/sensors"),
        }
    }
}

pub fn run(c: &Client, cfg: &Config) -> Result<(), String> {
    let mut terminal = ratatui::init();
    let res = event_loop(&mut terminal, c, cfg);
    ratatui::restore();
    res
}

fn event_loop(terminal: &mut DefaultTerminal, c: &Client, cfg: &Config) -> Result<(), String> {
    let refresh = Duration::from_millis(cfg.client.refresh_ms.max(250));
    let mut snap = Snapshot::fetch(c);
    let mut last = Instant::now();
    loop {
        terminal.draw(|f| draw(f, &snap, cfg)).map_err(|e| e.to_string())?;
        let wait = refresh.saturating_sub(last.elapsed());
        if event::poll(wait).map_err(|e| e.to_string())? {
            if let Event::Key(k) = event::read().map_err(|e| e.to_string())? {
                if k.kind == KeyEventKind::Press {
                    match k.code {
                        KeyCode::Char('q') | KeyCode::Esc => return Ok(()),
                        KeyCode::Char('c') if k.modifiers.contains(KeyModifiers::CONTROL) => return Ok(()),
                        KeyCode::Char('r') => {
                            snap = Snapshot::fetch(c);
                            last = Instant::now();
                        }
                        _ => {}
                    }
                }
            }
        }
        if last.elapsed() >= refresh {
            snap = Snapshot::fetch(c);
            last = Instant::now();
        }
    }
}

fn state_color(s: &str) -> Color {
    match s {
        "normal" | "ok" => Color::Green,
        "emergency" | "error" | "critical" => Color::Red,
        _ => Color::Yellow,
    }
}

fn draw(f: &mut Frame, snap: &Snapshot, cfg: &Config) {
    let fan_rows = match (&snap.fans, &snap.headers) {
        (Ok(fans), Ok(h)) => view::fan_rows(fans, h),
        _ => Vec::new(),
    };
    let [top, mid, bot, foot] = Layout::vertical([
        Constraint::Length(5),
        Constraint::Length(fan_rows.len().max(1) as u16 + 3),
        Constraint::Min(4),
        Constraint::Length(1),
    ])
    .areas(f.area());

    // Header: daemon state.
    let header_lines: Vec<Line> = match &snap.status {
        Ok(st) => {
            let g = |k: &str| st.get(k).and_then(Value::as_str).unwrap_or("?").to_string();
            let thermal = g("thermal_state");
            let overall = g("overall_status");
            let profile = snap.active.as_ref().map(view::active_profile).unwrap_or_else(|e| e.clone());
            vec![
                Line::from(vec![
                    Span::raw("ventulusd "),
                    Span::styled(g("daemon_version"), Style::default().add_modifier(Modifier::BOLD)),
                    Span::raw(format!("  up {}s   overall ", st.get("uptime_seconds").and_then(Value::as_u64).unwrap_or(0))),
                    Span::styled(overall.clone(), Style::default().fg(state_color(&overall))),
                    Span::raw("   thermal "),
                    Span::styled(thermal.clone(), Style::default().fg(state_color(&thermal)).add_modifier(Modifier::BOLD)),
                ]),
                Line::from(format!("profile  {profile}")),
                Line::from(format!(
                    "readiness {}",
                    st.pointer("/readiness/top_summary").and_then(Value::as_str).unwrap_or("-")
                )),
            ]
        }
        Err(e) => vec![Line::styled(format!("daemon unreachable: {e}"), Style::default().fg(Color::Red))],
    };
    f.render_widget(
        Paragraph::new(header_lines).block(Block::default().borders(Borders::ALL).title(" MiOS fan control ")),
        top,
    );

    // Fans.
    let rows = fan_rows.iter().map(|r| {
        let mode_style = if r.daemon_owned { Style::default().fg(Color::Cyan) } else { Style::default() };
        Row::new(vec![
            Cell::from(r.header.clone()),
            Cell::from(r.role.clone()),
            Cell::from(r.mode.clone()).style(mode_style),
            Cell::from(r.duty_pct.map(|d| format!("{d:>3}%")).unwrap_or("-".into())),
            Cell::from(view::duty_bar(r.duty_pct, 20)),
            Cell::from(r.rpm.map(|d| format!("{d:>5}")).unwrap_or("-".into())),
            Cell::from(r.flags.clone()).style(Style::default().fg(Color::Red)),
        ])
    });
    let fans_title = match &snap.fans {
        Ok(_) => " Fan headers ".to_string(),
        Err(e) => format!(" Fan headers: {e} "),
    };
    f.render_widget(
        Table::new(
            rows,
            [
                Constraint::Length(15),
                Constraint::Length(13),
                Constraint::Length(16),
                Constraint::Length(5),
                Constraint::Length(21),
                Constraint::Length(6),
                Constraint::Min(6),
            ],
        )
        .header(Row::new(vec!["HEADER", "ROLE", "MODE", "DUTY", "", "RPM", "FLAGS"]).style(Style::default().add_modifier(Modifier::BOLD)))
        .block(Block::default().borders(Borders::ALL).title(fans_title)),
        mid,
    );

    // Sensors.
    let srows: Vec<Row> = match &snap.sensors {
        Ok(v) => view::sensor_rows(v)
            .into_iter()
            .map(|s| {
                let hot = s.value_c.map(|t| t >= 85.0).unwrap_or(false);
                Row::new(vec![
                    Cell::from(s.kind),
                    Cell::from(s.chip),
                    Cell::from(s.label),
                    Cell::from(s.value_c.map(|t| format!("{t:>6.1} C")).unwrap_or("-".into()))
                        .style(if hot { Style::default().fg(Color::Red) } else { Style::default() }),
                ])
            })
            .collect(),
        Err(e) => vec![Row::new(vec![Cell::from(e.clone())])],
    };
    f.render_widget(
        Table::new(srows, [Constraint::Length(12), Constraint::Length(16), Constraint::Length(30), Constraint::Min(9)])
            .header(Row::new(vec!["KIND", "CHIP", "LABEL", "TEMP"]).style(Style::default().add_modifier(Modifier::BOLD)))
            .block(Block::default().borders(Borders::ALL).title(" Temperatures ")),
        bot,
    );

    f.render_widget(
        Paragraph::new(format!(
            " q quit  r refresh  |  {}  |  refresh {} ms  |  overrides: mios-fanctl override CONTROL PCT",
            cfg.client.socket, cfg.client.refresh_ms
        ))
        .style(Style::default().fg(Color::DarkGray)),
        foot,
    );
}
