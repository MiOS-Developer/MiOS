// AI-hint: Python-compatible renderings shared by the ports of Python generators: json.dumps(v, indent=2) with ensure_ascii, repr() of JSON-shaped values and float repr, and Python's == over JSON values (1 == 1.0 == True, dicts unordered), so a port reproduces its adapter's bytes and messages exactly.
// AI-related: tools/native/mios-gen/src/ai_metadata.rs, tools/native/mios-gen/src/dotfiles_sync.rs, tests/golden/README.md
// AI-functions: dumps, json_string, repr, float_repr, py_eq

use serde_json::Value;

/// `json.dumps(v, indent=2)` with the default `ensure_ascii=True`.
pub fn dumps(v: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, v, 0);
    out
}

/// `json.dumps(s)` of one string.
pub fn json_string(s: &str) -> String {
    let mut out = String::new();
    write_string(&mut out, s);
    out
}

fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

fn number(n: &serde_json::Number) -> String {
    match n.as_f64() {
        Some(f) if !n.is_i64() && !n.is_u64() => float_repr(f),
        _ => n.to_string(),
    }
}

fn write_value(out: &mut String, v: &Value, depth: usize) {
    let pad = |n: usize| "  ".repeat(n);
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&number(n)),
        Value::String(s) => write_string(out, s),
        Value::Array(items) if items.is_empty() => out.push_str("[]"),
        Value::Array(items) => {
            out.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&pad(depth + 1));
                write_value(out, item, depth + 1);
            }
            out.push('\n');
            out.push_str(&pad(depth));
            out.push(']');
        }
        Value::Object(map) if map.is_empty() => out.push_str("{}"),
        Value::Object(map) => {
            out.push_str("{\n");
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&pad(depth + 1));
                write_string(out, key);
                out.push_str(": ");
                write_value(out, item, depth + 1);
            }
            out.push('\n');
            out.push_str(&pad(depth));
            out.push('}');
        }
    }
}

/// `repr(float)`: the shortest round-trip digits, positional for decimal
/// exponents -4..16, else `d.ddde+XX`.
pub fn float_repr(f: f64) -> String {
    if f.is_nan() {
        return "nan".into();
    }
    if f.is_infinite() {
        return if f > 0.0 { "inf".into() } else { "-inf".into() };
    }
    // `{:e}` gives the shortest round-trip digits: "-1.25e-5", "1e16", "0e0".
    let sci = format!("{f:e}");
    let (mantissa, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let (sign, mantissa) = mantissa.strip_prefix('-').map_or(("", mantissa), |m| ("-", m));
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if (-4..16).contains(&exp) {
        let point = exp + 1; // digits before the decimal point
        let body = if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if point as usize >= digits.len() {
            format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
        } else {
            format!("{}.{}", &digits[..point as usize], &digits[point as usize..])
        };
        format!("{sign}{body}")
    } else {
        let frac = if digits.len() > 1 { format!(".{}", &digits[1..]) } else { String::new() };
        let esign = if exp < 0 { '-' } else { '+' };
        format!("{sign}{}{frac}e{esign}{:02}", &digits[..1], exp.abs())
    }
}

fn str_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            // str.isprintable(), restricted to the controls (the cases a
            // settings value or a TOML string can carry).
            c if (c as u32) < 0x20 || (0x7f..=0xa0).contains(&(c as u32)) || c == '\u{ad}' => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c if matches!(c as u32, 0x2028 | 0x2029 | 0xfeff) => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `repr()` of the Python object `json.load` (or tomllib) would have built.
pub fn repr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => number(n),
        Value::String(s) => str_repr(s),
        Value::Array(items) => format!("[{}]", items.iter().map(repr).collect::<Vec<_>>().join(", ")),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter().map(|(k, v)| format!("{}: {}", str_repr(k), repr(v))).collect::<Vec<_>>().join(", ")
        ),
    }
}

/// A JSON number or bool as Python compares it (bool is an int subclass).
fn numeric(v: &Value) -> Option<f64> {
    match v {
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        Value::Number(n) => n.as_f64(),
        _ => None,
    }
}

/// Python `==` over the objects `json.load` builds: 1 == 1.0 == True, and
/// dicts compare without regard to key order.
pub fn py_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) if x.is_i64() && y.is_i64() => x.as_i64() == y.as_i64(),
        (Value::Number(x), Value::Number(y)) if x.is_u64() && y.is_u64() => x.as_u64() == y.as_u64(),
        (Value::Null, Value::Null) => true,
        (Value::String(x), Value::String(y)) => x == y,
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| py_eq(p, q)),
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| py_eq(v, w)))
        }
        _ => match (numeric(a), numeric(b)) {
            (Some(x), Some(y)) => x == y,
            _ => false,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn dumps_matches_python_indent_2() {
        let v = json!({"a": [], "b": [1, {"c": null}], "d": "é\u{1F600}\u{7f}\"\\\n", "f": 1.5, "g": {}});
        assert_eq!(
            dumps(&v),
            "{\n  \"a\": [],\n  \"b\": [\n    1,\n    {\n      \"c\": null\n    }\n  ],\n  \"d\": \"\\u00e9\\ud83d\\ude00\\u007f\\\"\\\\\\n\",\n  \"f\": 1.5,\n  \"g\": {}\n}"
        );
    }

    #[test]
    fn float_repr_matches_python() {
        for (f, want) in [
            (1.5, "1.5"),
            (1.0, "1.0"),
            (0.1, "0.1"),
            (100.0, "100.0"),
            (1e16, "1e+16"),
            (1.25e16, "1.25e+16"),
            (1e-5, "1e-05"),
            (0.0001, "0.0001"),
            (-2.5e-7, "-2.5e-07"),
            (123456789012345.6, "123456789012345.6"),
            (0.0, "0.0"),
        ] {
            assert_eq!(float_repr(f), want, "{f}");
        }
    }

    #[test]
    fn repr_matches_python() {
        assert_eq!(repr(&json!(null)), "None");
        assert_eq!(repr(&json!([8080, true, "x"])), "[8080, True, 'x']");
        assert_eq!(repr(&json!({"cpus": 8, "memory": "32gb"})), "{'cpus': 8, 'memory': '32gb'}");
        assert_eq!(repr(&json!("it's")), "\"it's\"");
        assert_eq!(repr(&json!("both ' and \"")), "'both \\' and \"'");
        assert_eq!(repr(&json!("tab\tnl\n\u{1}é")), "'tab\\tnl\\n\\x01é'");
    }

    #[test]
    fn equality_follows_python() {
        assert!(py_eq(&json!(1), &json!(1.0)));
        assert!(py_eq(&json!(true), &json!(1)));
        assert!(!py_eq(&json!("1"), &json!(1)));
        assert!(py_eq(&json!({"a": 1, "b": 2}), &json!({"b": 2, "a": 1})));
        assert!(!py_eq(&json!([1, 2]), &json!([2, 1])));
        assert!(!py_eq(&json!(null), &json!(false)));
    }
}
