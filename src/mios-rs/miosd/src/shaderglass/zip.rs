// AI-hint: miosd shaderglass zip: a minimal, bounded ZIP reader (central directory, stored and deflate members, CRC-32 checked) for the ShaderGlass release archive; only named root members are ever read.
// AI-related: src/mios-rs/miosd/src/shaderglass/release.rs
//! Enough ZIP for a release archive whose digest was already verified: the end-of-central-directory
//! record, the central directory, and stored (0) or deflate (8) members. ZIP64, encryption and
//! multi-disk archives are refused. Every member is size-capped and CRC-checked, and callers ask
//! for exact root names, so an archive path can never choose where something is written.

use std::io::Read;

/// Largest member we inflate (ShaderGlass.exe is ~80 MB with every shader compiled in).
pub const MAX_MEMBER: u64 = 512 * 1024 * 1024;

#[derive(Clone, Debug, PartialEq)]
pub struct Entry {
    pub name: String,
    method: u16,
    flags: u16,
    crc: u32,
    compressed: u64,
    size: u64,
    offset: u64,
}

fn u16_at(d: &[u8], at: usize) -> Result<u16, String> {
    d.get(at..at + 2)
        .map(|b| u16::from_le_bytes([b[0], b[1]]))
        .ok_or_else(|| "zip: truncated".to_string())
}

fn u32_at(d: &[u8], at: usize) -> Result<u32, String> {
    d.get(at..at + 4)
        .map(|b| u32::from_le_bytes([b[0], b[1], b[2], b[3]]))
        .ok_or_else(|| "zip: truncated".to_string())
}

/// The central directory's entries.
pub fn entries(d: &[u8]) -> Result<Vec<Entry>, String> {
    const EOCD: u32 = 0x0605_4b50;
    const CEN: u32 = 0x0201_4b50;
    if d.len() < 22 {
        return Err("zip: too short".into());
    }
    let lowest = d.len().saturating_sub(22 + 65_535);
    let eocd = (lowest..=d.len() - 22)
        .rev()
        .find(|&i| u32_at(d, i).ok() == Some(EOCD))
        .ok_or("zip: no end-of-central-directory record")?;
    if u16_at(d, eocd + 4)? != 0 || u16_at(d, eocd + 6)? != 0 {
        return Err("zip: multi-disk archives are not supported".into());
    }
    let count = u16_at(d, eocd + 10)? as usize;
    let size = u32_at(d, eocd + 12)? as usize;
    let start = u32_at(d, eocd + 16)? as usize;
    if count == 0xFFFF || size == 0xFFFF_FFFF || start == 0xFFFF_FFFF {
        return Err("zip: ZIP64 archives are not supported".into());
    }
    if start.checked_add(size).is_none_or(|end| end > eocd) {
        return Err("zip: central directory out of bounds".into());
    }
    let mut out = Vec::with_capacity(count);
    let mut at = start;
    for _ in 0..count {
        if u32_at(d, at)? != CEN {
            return Err("zip: bad central directory entry".into());
        }
        let name_len = u16_at(d, at + 28)? as usize;
        let extra_len = u16_at(d, at + 30)? as usize;
        let comment_len = u16_at(d, at + 32)? as usize;
        let name = d
            .get(at + 46..at + 46 + name_len)
            .ok_or("zip: truncated name")?;
        let e = Entry {
            name: String::from_utf8_lossy(name).into_owned(),
            flags: u16_at(d, at + 8)?,
            method: u16_at(d, at + 10)?,
            crc: u32_at(d, at + 16)?,
            compressed: u64::from(u32_at(d, at + 20)?),
            size: u64::from(u32_at(d, at + 24)?),
            offset: u64::from(u32_at(d, at + 42)?),
        };
        if e.compressed == 0xFFFF_FFFF || e.size == 0xFFFF_FFFF || e.offset == 0xFFFF_FFFF {
            return Err("zip: ZIP64 members are not supported".into());
        }
        out.push(e);
        at += 46 + name_len + extra_len + comment_len;
    }
    Ok(out)
}

/// The member called exactly `name` at the archive root (a single path component).
pub fn find<'a>(list: &'a [Entry], name: &str) -> Result<Option<&'a Entry>, String> {
    if name.is_empty() || name.contains('/') || name.contains('\\') || name == "." || name == ".." {
        return Err(format!("zip: {name:?} is not a plain root member name"));
    }
    let mut hits = list.iter().filter(|e| e.name == name);
    let first = hits.next();
    if hits.next().is_some() {
        return Err(format!("zip: {name} appears more than once"));
    }
    Ok(first)
}

/// Read one member, inflated, length- and CRC-checked.
pub fn read(d: &[u8], e: &Entry) -> Result<Vec<u8>, String> {
    const LOC: u32 = 0x0403_4b50;
    if e.flags & 1 != 0 {
        return Err(format!("zip: {} is encrypted", e.name));
    }
    if e.size > MAX_MEMBER || e.compressed > MAX_MEMBER {
        return Err(format!("zip: {} is larger than {MAX_MEMBER} bytes", e.name));
    }
    let off = e.offset as usize;
    if u32_at(d, off)? != LOC {
        return Err(format!("zip: {} has no local header", e.name));
    }
    let data_at = off + 30 + u16_at(d, off + 26)? as usize + u16_at(d, off + 28)? as usize;
    let raw = d
        .get(data_at..data_at + e.compressed as usize)
        .ok_or_else(|| format!("zip: {} is truncated", e.name))?;
    let out = match e.method {
        0 => raw.to_vec(),
        8 => {
            let mut v = Vec::with_capacity(e.size as usize);
            flate2::read::DeflateDecoder::new(raw)
                .take(e.size + 1)
                .read_to_end(&mut v)
                .map_err(|err| format!("zip: {}: {err}", e.name))?;
            v
        }
        m => return Err(format!("zip: {} uses unsupported method {m}", e.name)),
    };
    if out.len() as u64 != e.size {
        return Err(format!("zip: {} inflated to {} bytes, expected {}", e.name, out.len(), e.size));
    }
    let mut crc = flate2::Crc::new();
    crc.update(&out);
    if crc.sum() != e.crc {
        return Err(format!("zip: {} fails its CRC-32", e.name));
    }
    Ok(out)
}

/// Build an archive (tests): `(name, data, deflate)`.
#[cfg(test)]
pub fn build(members: &[(&str, &[u8], bool)]) -> Vec<u8> {
    use std::io::Write;
    let mut out = Vec::new();
    let mut central = Vec::new();
    for (name, data, deflate) in members {
        let mut crc = flate2::Crc::new();
        crc.update(data);
        let body = if *deflate {
            let mut enc = flate2::write::DeflateEncoder::new(Vec::new(), flate2::Compression::default());
            enc.write_all(data).unwrap();
            enc.finish().unwrap()
        } else {
            data.to_vec()
        };
        let method: u16 = if *deflate { 8 } else { 0 };
        let offset = out.len() as u32;
        out.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
        out.extend_from_slice(&20u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&method.to_le_bytes());
        out.extend_from_slice(&[0; 4]);
        out.extend_from_slice(&crc.sum().to_le_bytes());
        out.extend_from_slice(&(body.len() as u32).to_le_bytes());
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&(name.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(name.as_bytes());
        out.extend_from_slice(&body);
        central.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
        central.extend_from_slice(&20u16.to_le_bytes());
        central.extend_from_slice(&20u16.to_le_bytes());
        central.extend_from_slice(&0u16.to_le_bytes());
        central.extend_from_slice(&method.to_le_bytes());
        central.extend_from_slice(&[0; 4]);
        central.extend_from_slice(&crc.sum().to_le_bytes());
        central.extend_from_slice(&(body.len() as u32).to_le_bytes());
        central.extend_from_slice(&(data.len() as u32).to_le_bytes());
        central.extend_from_slice(&(name.len() as u16).to_le_bytes());
        central.extend_from_slice(&[0; 12]);
        central.extend_from_slice(&offset.to_le_bytes());
        central.extend_from_slice(name.as_bytes());
    }
    let start = out.len() as u32;
    out.extend_from_slice(&central);
    out.extend_from_slice(&0x0605_4b50u32.to_le_bytes());
    out.extend_from_slice(&[0; 4]);
    out.extend_from_slice(&(members.len() as u16).to_le_bytes());
    out.extend_from_slice(&(members.len() as u16).to_le_bytes());
    out.extend_from_slice(&(central.len() as u32).to_le_bytes());
    out.extend_from_slice(&start.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_stored_and_deflated_root_members() {
        let exe = b"MZ\x90\x00 pretend windows binary".repeat(50);
        let z = build(&[("ShaderGlass.exe", &exe, true), ("LICENSE", b"GNU GENERAL PUBLIC LICENSE\nVersion 3", false)]);
        let list = entries(&z).unwrap();
        assert_eq!(list.len(), 2);
        assert_eq!(read(&z, find(&list, "ShaderGlass.exe").unwrap().unwrap()).unwrap(), exe);
        assert!(read(&z, find(&list, "LICENSE").unwrap().unwrap()).unwrap().starts_with(b"GNU GENERAL"));
        assert!(find(&list, "WineCap.dll").unwrap().is_none());
    }

    #[test]
    fn refuses_paths_duplicates_corruption_and_truncation() {
        let z = build(&[("a/../../evil", b"x", false), ("dup", b"1", false), ("dup", b"2", false)]);
        let list = entries(&z).unwrap();
        for bad in ["a/../../evil", "../x", "", ".."] {
            assert!(find(&list, bad).is_err(), "{bad}");
        }
        assert!(find(&list, "dup").is_err(), "duplicate names are ambiguous");
        // Flip one payload byte: the CRC catches it.
        let mut z = build(&[("f", b"hello world", false)]);
        let at = z.windows(11).position(|w| w == b"hello world").unwrap();
        z[at] ^= 1;
        let list = entries(&z).unwrap();
        assert!(read(&z, &list[0]).unwrap_err().contains("CRC"));
        assert!(entries(&z[..z.len() - 30]).is_err(), "truncated archive");
        assert!(entries(b"not a zip at all, just bytes").is_err());
    }
}
