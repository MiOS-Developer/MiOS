// AI-hint: mios-controls input: one dedicated OS thread owns terminal input -- a blocking crossterm read, pinned to its own CPU ([desktop.controls].input_cpu, default the last online CPU) while every other thread of the app is kept off that CPU -- so input is never delayed by drawing, validation subprocesses or file IO.
// AI-related: tools/native/mios-controls/src/tui.rs, usr/share/mios/mios.toml [desktop.controls]
// AI-functions: spawn, choose_cpu, unpin_others
use crate::tui::Msg;
use ratatui::crossterm::event;
use rustix::process::{sched_getaffinity, sched_setaffinity, CpuSet};
use std::sync::mpsc::Sender;

/// The CPUs this process may run on.
fn allowed() -> Vec<usize> {
    match sched_getaffinity(None) {
        Ok(set) => (0..CpuSet::MAX_CPU).filter(|c| set.is_set(*c)).collect(),
        Err(_) => Vec::new(),
    }
}

/// The input CPU: the configured one when it is allowed, else the last allowed CPU. None on a one-CPU affinity
/// (nothing can be reserved there; the thread still exists and still blocks only on input).
pub fn choose_cpu(configured: Option<i64>, allowed: &[usize]) -> Option<usize> {
    if allowed.len() < 2 {
        return None;
    }
    match configured {
        Some(c) if c >= 0 && allowed.contains(&(c as usize)) => Some(c as usize),
        _ => allowed.last().copied(),
    }
}

fn set(cpus: impl IntoIterator<Item = usize>) -> CpuSet {
    let mut s = CpuSet::new();
    for c in cpus {
        s.set(c);
    }
    s
}

/// Keep the calling thread (render/validation/IO) off the input CPU.
pub fn unpin_others(input_cpu: usize, allowed: &[usize]) -> Result<(), String> {
    sched_setaffinity(None, &set(allowed.iter().copied().filter(|c| *c != input_cpu))).map_err(|e| e.to_string())
}

/// Start the input thread. It does nothing but block in event::read and forward every event; it is the only
/// caller of crossterm's reader. The calling thread is moved off the input CPU afterwards, and threads it spawns
/// later inherit that affinity.
/// Returns a one-line description of where the input thread runs (for --help diagnostics, not the UI).
pub fn spawn(tx: Sender<Msg>, configured_cpu: Option<i64>) -> String {
    let allowed = allowed();
    let cpu = choose_cpu(configured_cpu, &allowed);
    let (ready_tx, ready_rx) = std::sync::mpsc::channel::<Result<(), String>>();
    std::thread::Builder::new()
        .name("mios-controls-input".into())
        .spawn(move || {
            let pinned = match cpu {
                Some(c) => sched_setaffinity(None, &set([c])).map_err(|e| e.to_string()),
                None => Ok(()),
            };
            let _ = ready_tx.send(pinned);
            loop {
                match event::read() {
                    Ok(ev) => {
                        if tx.send(Msg::Input(ev)).is_err() {
                            return;
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(Msg::InputFailed(e.to_string()));
                        return;
                    }
                }
            }
        })
        .expect("spawn the input thread");
    let pin = ready_rx.recv().unwrap_or_else(|_| Err("input thread exited".into()));
    let note = match (cpu, pin) {
        (Some(c), Ok(())) => match unpin_others(c, &allowed) {
            Ok(()) => format!("input thread on CPU {c} (app threads elsewhere)"),
            Err(e) => format!("input thread on CPU {c}; could not move the app off it: {e}"),
        },
        (Some(c), Err(e)) => format!("input thread not pinned to CPU {c}: {e}"),
        (None, _) => "input thread unpinned (single-CPU affinity)".into(),
    };
    note
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpu_choice_prefers_configured_then_last_and_never_the_only_cpu() {
        assert_eq!(choose_cpu(Some(2), &[0, 1, 2, 3]), Some(2));
        assert_eq!(choose_cpu(None, &[0, 1, 2, 3]), Some(3));
        assert_eq!(choose_cpu(Some(9), &[0, 1, 2, 3]), Some(3), "a CPU outside the affinity falls back");
        assert_eq!(choose_cpu(Some(-1), &[0, 5]), Some(5));
        assert_eq!(choose_cpu(Some(0), &[0]), None, "one CPU cannot be reserved");
        assert_eq!(choose_cpu(None, &[]), None);
    }

    #[test]
    fn pinning_this_thread_and_moving_others_off_it_round_trips() {
        let allowed = allowed();
        let Some(cpu) = choose_cpu(None, &allowed) else { return };
        std::thread::spawn(move || {
            sched_setaffinity(None, &set([cpu])).unwrap();
            let now = sched_getaffinity(None).unwrap();
            assert!(now.is_set(cpu) && (0..CpuSet::MAX_CPU).filter(|c| now.is_set(*c)).count() == 1);
            unpin_others(cpu, &allowed).unwrap();
            let now = sched_getaffinity(None).unwrap();
            assert!(!now.is_set(cpu), "the app thread left the input CPU");
        })
        .join()
        .unwrap();
    }
}
