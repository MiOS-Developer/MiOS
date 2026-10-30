// AI-hint: mios-wl-fixtures library: the pure argument contract of the Wayland test clients (examples/), host-tested.
//          mios-subsurface-churn takes <fps> <life_frames> <seconds> and is always bounded (at most 600 s).
// AI-related: tools/native/mios-wl-fixtures/examples/mios-subsurface-churn.rs, tests/test-window-fps-live.py
// AI-functions: parse_args

#![forbid(unsafe_code)]

/// `<fps> <life_frames> <seconds>`: fps in (0, 1000], life_frames >= 1, seconds in (0, 600] (never unbounded).
pub fn parse_args(args: &[String]) -> Result<(f64, u64, f64), String> {
    let [fps, life, secs] = args else {
        return Err("usage: mios-subsurface-churn <fps> <life_frames> <seconds>".into());
    };
    let fps: f64 = fps.parse().map_err(|_| format!("bad fps {fps:?}"))?;
    let life: u64 = life.parse().map_err(|_| format!("bad life_frames {life:?}"))?;
    let secs: f64 = secs.parse().map_err(|_| format!("bad seconds {secs:?}"))?;
    if !(fps > 0.0 && fps <= 1000.0) || life == 0 || !(secs > 0.0 && secs <= 600.0) {
        return Err("fps in (0, 1000], life_frames >= 1, seconds in (0, 600]".into());
    }
    Ok((fps, life, secs))
}

#[cfg(test)]
mod tests {
    use super::parse_args;

    fn a(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn arguments_are_bounded() {
        assert_eq!(parse_args(&a(&["30", "3", "10"])), Ok((30.0, 3, 10.0)));
        assert!(parse_args(&a(&["0", "3", "10"])).is_err());
        assert!(parse_args(&a(&["30", "0", "10"])).is_err());
        assert!(parse_args(&a(&["30", "3", "601"])).is_err(), "never unbounded");
        assert!(parse_args(&a(&["30", "3"])).is_err());
    }
}
