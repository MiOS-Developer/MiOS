// AI-hint: cfg(test) observation only; worker routing and results are unchanged.
use std::sync::{Mutex, OnceLock};
fn records() -> &'static Mutex<Vec<serde_json::Value>> {
    static RECORDS: OnceLock<Mutex<Vec<serde_json::Value>>> = OnceLock::new();
    RECORDS.get_or_init(|| Mutex::new(vec![]))
}
pub(crate) fn record_result<T>(kind: &str, result: &Result<T, String>) {
    let value = serde_json::json!({"kind":kind,"ok":result.is_ok(),"error":result.as_ref().err()});
    records().lock().unwrap().push(value.clone());
    completed().notify_all();
    trace("worker-result", value);
}
pub(crate) fn take() -> Vec<serde_json::Value> {
    std::mem::take(&mut *records().lock().unwrap())
}
pub(crate) fn trace(id: &str, value: serde_json::Value) {
    let row = serde_json::json!({"case":id,"evidence":value});
    eprintln!("{row}");
    if let Some(path) = std::env::var_os("MIOS_CONTROLLER_ACCEPTANCE_LEDGER") {
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .create(true)
            .open(path)
            .unwrap();
        writeln!(file, "{row}").unwrap();
    }
}

fn completed() -> &'static std::sync::Condvar {
    static COMPLETED: std::sync::Condvar = std::sync::Condvar::new();
    &COMPLETED
}
pub(crate) fn wait_for_results(count: usize) {
    let until = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let mut rows = records().lock().unwrap();
    while rows.len() < count {
        let left = until.saturating_duration_since(std::time::Instant::now());
        assert!(
            !left.is_zero(),
            "actual worker result deadline: wanted {count}, got {}",
            rows.len()
        );
        rows = completed().wait_timeout(rows, left).unwrap().0;
    }
}
pub(crate) type EffectHook = std::sync::Arc<dyn Fn(&str) + Send + Sync>;
fn effect_hook() -> &'static Mutex<Option<EffectHook>> {
    static HOOK: OnceLock<Mutex<Option<EffectHook>>> = OnceLock::new();
    HOOK.get_or_init(|| Mutex::new(None))
}
pub(crate) fn set_effect_hook(hook: Option<EffectHook>) {
    *effect_hook().lock().unwrap() = hook;
}
pub(crate) fn effect_boundary(phase: &str) {
    let hook = effect_hook().lock().unwrap().clone();
    if let Some(hook) = hook {
        hook(phase);
    }
}
