// AI-hint: Pure presentation helpers shared by the mios-fanctl CLI tables and the TUI (row models built from ventulusd JSON).
// AI-related: tools/native/mios-fanctl/src/main.rs, tools/native/mios-fanctl/src/tui.rs
// AI-functions: short_header, mode_label, fan_rows, sensor_rows, duty_bar, active_profile

use serde_json::Value;

pub struct FanRow {
    pub header: String,
    pub id: String,
    pub role: String,
    pub mode: String,
    pub daemon_owned: bool,
    pub duty_pct: Option<u64>,
    pub rpm: Option<u64>,
    pub flags: String,
}

pub struct SensorRow {
    pub kind: String,
    pub chip: String,
    pub label: String,
    pub value_c: Option<f64>,
}

/// `hwmon:nct6799:nct6775.656:pwm4:pwm4` -> `nct6799/pwm4`.
pub fn short_header(id: &str) -> String {
    let p: Vec<&str> = id.split(':').collect();
    if p.first() == Some(&"hwmon") && p.len() >= 5 {
        format!("{}/{}", p[1], p[p.len() - 2])
    } else {
        id.to_string()
    }
}

/// hwmon ABI `pwmN_enable`: 0 = no control (full speed), 1 = manual (held by
/// the daemon), anything else = a firmware/chip automatic mode.
pub fn mode_label(enable: Option<u64>) -> (String, bool) {
    match enable {
        Some(1) => ("daemon (manual)".into(), true),
        Some(0) => ("full speed".into(), false),
        Some(n) => (format!("firmware ({n})"), false),
        None => ("-".into(), false),
    }
}

pub fn fan_rows(fans: &Value, headers: &Value) -> Vec<FanRow> {
    let empty = Vec::new();
    let hdrs = headers.get("headers").and_then(Value::as_array).unwrap_or(&empty);
    let mut rows: Vec<FanRow> = fans
        .get("fans")
        .and_then(Value::as_array)
        .unwrap_or(&empty)
        .iter()
        .map(|f| {
            let id = f.get("id").and_then(Value::as_str).unwrap_or("?").to_string();
            let h = hdrs.iter().find(|h| h.get("id").and_then(Value::as_str) == Some(id.as_str()));
            let role = h
                .and_then(|h| h.get("role"))
                .and_then(Value::as_str)
                .unwrap_or("-")
                .to_string();
            let (mode, daemon_owned) = mode_label(f.get("pwm_enable_mode").and_then(Value::as_u64));
            let mut flags = Vec::new();
            if f.get("stall_detected").and_then(Value::as_bool) == Some(true) {
                flags.push("STALL");
            }
            if f.get("fan_alarm").and_then(Value::as_bool) == Some(true) {
                flags.push("ALARM");
            }
            if f.get("duty_not_holding").and_then(Value::as_bool) == Some(true) {
                flags.push("NOT-HOLDING");
            }
            FanRow {
                header: short_header(&id),
                id,
                role,
                mode,
                daemon_owned,
                duty_pct: f
                    .get("pwm_readback_pct")
                    .and_then(Value::as_u64)
                    .or_else(|| f.get("last_commanded_pwm").and_then(Value::as_u64)),
                rpm: f.get("rpm").and_then(Value::as_u64),
                flags: flags.join(","),
            }
        })
        .collect();
    rows.sort_by(|a, b| a.id.cmp(&b.id));
    rows
}

fn kind_rank(kind: &str) -> u8 {
    match kind {
        "cpu_temp" => 0,
        "coolant_temp" => 1,
        "gpu_temp" => 2,
        "mb_temp" => 3,
        "disk_temp" => 4,
        _ => 5,
    }
}

pub fn sensor_rows(sensors: &Value) -> Vec<SensorRow> {
    let empty = Vec::new();
    let mut rows: Vec<SensorRow> = sensors
        .get("sensors")
        .and_then(Value::as_array)
        .unwrap_or(&empty)
        .iter()
        .map(|s| SensorRow {
            kind: s.get("kind").and_then(Value::as_str).unwrap_or("?").to_string(),
            chip: s.get("chip_name").and_then(Value::as_str).unwrap_or("?").to_string(),
            label: s.get("label").and_then(Value::as_str).unwrap_or("?").to_string(),
            value_c: s.get("value_c").and_then(Value::as_f64),
        })
        .collect();
    rows.sort_by(|a, b| {
        (kind_rank(&a.kind), &a.chip, &a.label).cmp(&(kind_rank(&b.kind), &b.chip, &b.label))
    });
    rows
}

pub fn duty_bar(pct: Option<u64>, width: usize) -> String {
    let Some(p) = pct else { return "-".repeat(width) };
    let filled = ((p.min(100) as usize) * width + 50) / 100;
    format!("{}{}", "#".repeat(filled), ".".repeat(width - filled))
}

pub fn active_profile(active: &Value) -> String {
    if active.get("active").and_then(Value::as_bool) == Some(true) {
        format!(
            "{} ({})",
            active.get("profile_id").and_then(Value::as_str).unwrap_or("?"),
            active.get("profile_name").and_then(Value::as_str).unwrap_or("")
        )
    } else {
        "none (every header on firmware control)".into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn shortens_hwmon_ids() {
        assert_eq!(short_header("hwmon:nct6799:nct6775.656:pwm4:pwm4"), "nct6799/pwm4");
        assert_eq!(short_header("openfan:ch00"), "openfan:ch00");
    }

    #[test]
    fn modes() {
        assert_eq!(mode_label(Some(1)), ("daemon (manual)".to_string(), true));
        assert_eq!(mode_label(Some(5)).0, "firmware (5)");
        assert!(!mode_label(Some(0)).1);
    }

    #[test]
    fn rows_join_roles_and_flags() {
        let fans = json!({"fans":[{"id":"hwmon:c:d:pwm6:pwm6","rpm":3300,"pwm_enable_mode":5,"pwm_readback_pct":100,"stall_detected":true}]});
        let headers = json!({"headers":[{"id":"hwmon:c:d:pwm6:pwm6","role":"pump"}]});
        let r = fan_rows(&fans, &headers);
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].role, "pump");
        assert_eq!(r[0].rpm, Some(3300));
        assert_eq!(r[0].flags, "STALL");
    }

    #[test]
    fn cpu_sensors_first() {
        let s = json!({"sensors":[{"kind":"disk_temp","chip_name":"nvme","label":"x","value_c":30.0},{"kind":"cpu_temp","chip_name":"k10temp","label":"Tctl","value_c":60.0}]});
        assert_eq!(sensor_rows(&s)[0].label, "Tctl");
    }

    #[test]
    fn bar() {
        assert_eq!(duty_bar(Some(50), 10), "#####.....");
        assert_eq!(duty_bar(Some(150), 4), "####");
        assert_eq!(duty_bar(None, 3), "---");
    }

    #[test]
    fn profile_label() {
        assert!(active_profile(&json!({"active":false})).starts_with("none"));
        assert_eq!(active_profile(&json!({"active":true,"profile_id":"a","profile_name":"A"})), "a (A)");
    }
}
