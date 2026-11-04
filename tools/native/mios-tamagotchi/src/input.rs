// AI-hint: Terminal keys shared by keyboard and existing Inputd key projections; no subscription IPC.
// AI-related: tools/native/mios-inputd/src/nav.rs, tools/native/mios-inputd/src/sessions.rs
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Quit,
    Pause,
    Previous,
    Next,
}

pub fn action(key: KeyEvent) -> Option<Action> {
    if key.kind == KeyEventKind::Release {
        return None;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) {
        return (key.code == KeyCode::Char('c')).then_some(Action::Quit);
    }
    if key.modifiers.intersects(
        KeyModifiers::ALT | KeyModifiers::SUPER | KeyModifiers::HYPER | KeyModifiers::META,
    ) {
        return None;
    }
    match key.code {
        KeyCode::Up | KeyCode::Left | KeyCode::BackTab => Some(Action::Previous),
        KeyCode::Down | KeyCode::Right | KeyCode::Tab => Some(Action::Next),
        KeyCode::Esc | KeyCode::Char('q') => Some(Action::Quit),
        KeyCode::Enter | KeyCode::Char('p') | KeyCode::Char(' ')
            if key.kind == KeyEventKind::Press =>
        {
            Some(Action::Pause)
        }
        _ => None,
    }
}
