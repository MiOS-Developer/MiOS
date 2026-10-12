// AI-hint: Installed compatibility command; the names projection belongs to mios-gen.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let root = match std::env::var_os("MIOS_DRIFT_ROOT") {
        Some(path) => std::path::PathBuf::from(path),
        None => std::env::current_dir()?,
    };
    // --check compares in memory and writes nothing: a drift gate must not
    // regenerate the tree it grades.
    if std::env::args().nth(1).as_deref() == Some("--check") {
        return mios_gen::names_registry::check(&root);
    }
    mios_gen::names_registry::run(&root)
}
