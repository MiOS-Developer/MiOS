// AI-hint: Keyboard and Inputd projected-key actions; releases never act and explicit repeat events only navigate.
// AI-related: game.rs, ui.rs, tools/native/mios-inputd/src/nav.rs
use crate::game::{Care, Direction};
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Quit,
    Cancel,
    Pause,
    Previous,
    Next,
    Accept,
    Save,
    Reset,
    Confirm,
    Menu,
    Status,
    Graphics,
    Help,
    Direction(Direction),
    Care(Care),
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
    let direction = match key.code {
        KeyCode::Up => Some(Direction::Up),
        KeyCode::Down => Some(Direction::Down),
        KeyCode::Left => Some(Direction::Left),
        KeyCode::Right => Some(Direction::Right),
        _ => None,
    };
    if let Some(d) = direction {
        return Some(Action::Direction(d));
    }
    match key.code {
        KeyCode::Tab => Some(Action::Next),
        KeyCode::BackTab => Some(Action::Previous),
        _ if key.kind != KeyEventKind::Press => None,
        KeyCode::Char('q') => Some(Action::Quit),
        KeyCode::Esc => Some(Action::Cancel),
        KeyCode::Enter | KeyCode::Char(' ') => Some(Action::Accept),
        KeyCode::Char('p') => Some(Action::Pause),
        KeyCode::Char('s') => Some(Action::Save),
        KeyCode::Char('n') => Some(Action::Reset),
        KeyCode::Char('y') => Some(Action::Confirm),
        KeyCode::Char('o') | KeyCode::F(1) => Some(Action::Menu),
        KeyCode::Char('i') => Some(Action::Status),
        KeyCode::Char('v') => Some(Action::Graphics),
        KeyCode::Char('?') => Some(Action::Help),
        KeyCode::Char('f') => Some(Action::Care(Care::Feed)),
        KeyCode::Char('g') => Some(Action::Care(Care::Play)),
        KeyCode::Char('c') => Some(Action::Care(Care::Clean)),
        KeyCode::Char('r') => Some(Action::Care(Care::Rest)),
        KeyCode::Char('m') => Some(Action::Care(Care::Medicine)),
        _ => None,
    }
}
