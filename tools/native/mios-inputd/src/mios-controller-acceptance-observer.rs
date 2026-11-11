// AI-hint: cfg(test) observation only; worker routing and results are unchanged.
use std::sync::{Mutex, OnceLock};
fn records() -> &'static Mutex<Vec<serde_json::Value>> {
    static RECORDS: OnceLock<Mutex<Vec<serde_json::Value>>> = OnceLock::new();
    RECORDS.get_or_init(|| Mutex::new(vec![]))
}
pub(crate) fn record_result<T>(kind: &str, result: &Result<T, String>) {
    let value = serde_json::json!({"kind":kind,"ok":result.is_ok(),"error":result.as_ref().err()});
    records().lock().unwrap().push(value.clone());
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
