use std::path::Path;

fn main() {
    println!("cargo:rustc-link-search=native=/usr/lib64");
    if let Ok(out_dir) = std::env::var("OUT_DIR") {
        let out = Path::new(&out_dir);
        for (soname, real) in [("libgbm.so", "/usr/lib64/libgbm.so.1"), ("libpam.so", "/usr/lib64/libpam.so.0")] {
            if !Path::new(&format!("/usr/lib64/{soname}")).exists() && Path::new(real).exists() {
                let dst = out.join(soname);
                let _ = std::fs::remove_file(&dst);
                let _ = std::os::unix::fs::symlink(real, &dst);
            }
        }
        println!("cargo:rustc-link-search=native={out_dir}");
    }
}
