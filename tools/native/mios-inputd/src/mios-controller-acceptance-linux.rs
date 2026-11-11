// AI-hint: cfg(test) regular-file descriptors only; no ioctl discovery, grab or uinput creation.
impl Device {
    pub(crate) fn acceptance_file(path: &Path, axes: BTreeMap<u16, Axis>) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path)?;
        assert!(file.metadata()?.is_file());
        Ok(Self {
            file,
            path: path.to_owned(),
            name: "mios-acceptance-file".into(),
            axes,
            keys: vec![],
            abs: BTreeMap::new(),
            id: InputId::default(),
            grabbed: false,
            remap: BTreeMap::new(),
            unmap: BTreeMap::new(),
            quirks: vec![],
        })
    }
}
impl VirtualDevice {
    pub(crate) fn acceptance_file(path: &Path) -> io::Result<Self> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path)?;
        assert!(file.metadata()?.is_file());
        Ok(Self {
            file,
            name: "mios-acceptance-file".into(),
        })
    }
}
pub(crate) fn acceptance_events(path: &Path) -> io::Result<Vec<Event>> {
    let bytes = std::fs::read(path)?;
    let size = std::mem::size_of::<InputEvent>();
    assert_eq!(bytes.len() % size, 0, "partial private sink record");
    Ok(bytes
        .chunks_exact(size)
        .map(|chunk| {
            // SAFETY: complete initialized repr(C) InputEvent written by VirtualDevice::write.
            let e = unsafe { std::ptr::read_unaligned(chunk.as_ptr().cast::<InputEvent>()) };
            Event::new(e.kind, e.code, e.value)
        })
        .collect())
}
