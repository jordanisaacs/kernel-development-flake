//! System initialization - kernel filesystem mounts

use anyhow::{Context, Result};
use rustix::fs::Mode;
use rustix::mount::{mount, MountFlags};

struct KernelMount {
    source: &'static str,
    target: &'static str,
    fstype: &'static str,
    flags: MountFlags,
    data: &'static str,
}

const KERNEL_MOUNTS: &[KernelMount] = &[
    KernelMount {
        source: "proc",
        target: "/proc",
        fstype: "proc",
        flags: MountFlags::empty(),
        data: "",
    },
    KernelMount {
        source: "sysfs",
        target: "/sys",
        fstype: "sysfs",
        flags: MountFlags::empty(),
        data: "",
    },
    KernelMount {
        source: "devtmpfs",
        target: "/dev",
        fstype: "devtmpfs",
        flags: MountFlags::empty(),
        data: "",
    },
    KernelMount {
        source: "tmpfs",
        target: "/run",
        fstype: "tmpfs",
        flags: MountFlags::empty(),
        data: "mode=0755",
    },
];

pub fn mount_kernel_filesystems() -> Result<()> {
    for m in KERNEL_MOUNTS {
        // Create mount point if it doesn't exist
        rustix::fs::mkdir(m.target, Mode::from_raw_mode(0o755))
            .or_else(|e| if e == rustix::io::Errno::EXIST { Ok(()) } else { Err(e) })
            .with_context(|| format!("Failed to create {}", m.target))?;

        // Mount filesystem
        mount(m.source, m.target, m.fstype, m.flags, m.data)
            .with_context(|| format!("Failed to mount {}", m.target))?;

        println!("kdf-init: mounted {}", m.target);
    }

    Ok(())
}

pub fn load_kernel_modules(modules_dir: Option<&str>) -> Result<()> {
    use rustix::fd::AsFd;
    use std::fs;

    // If no moddir specified, skip module loading
    let Some(modules_dir) = modules_dir else {
        println!("kdf-init: no moddir specified, skipping module loading");
        return Ok(());
    };

    println!("kdf-init: loading kernel modules from '{}'", modules_dir);

    // Check if modules directory exists
    if !std::path::Path::new(modules_dir).exists() {
        println!("kdf-init: moddir '{}' does not exist, skipping module loading", modules_dir);
        return Ok(());
    }

    // Read all files in modules directory
    let entries = fs::read_dir(modules_dir)
        .with_context(|| format!("Failed to read directory {}", modules_dir))?;

    let mut loaded_count = 0;
    let mut failed_count = 0;
    let mut total_count = 0;

    for entry in entries {
        let entry = entry.context("Failed to read directory entry")?;
        let path = entry.path();

        // Only process .ko files (including compressed ones)
        if let Some(ext) = path.extension() {
            let ext_str = ext.to_string_lossy();
            if ext_str == "ko" || path.to_string_lossy().ends_with(".ko.xz") || path.to_string_lossy().ends_with(".ko.gz") {
                total_count += 1;
                let file_name = path.file_name().unwrap().to_string_lossy();
                println!("kdf-init: loading module {}", file_name);

                match fs::File::open(&path) {
                    Ok(file) => {
                        use std::ffi::CStr;
                        let empty_params = CStr::from_bytes_with_nul(b"\0").unwrap();
                        match rustix::system::finit_module(file.as_fd(), empty_params, 0) {
                            Ok(_) => {
                                println!("kdf-init: successfully loaded {}", file_name);
                                loaded_count += 1;
                            }
                            Err(e) => {
                                println!("kdf-init: failed to load {}: {} (errno: {:?})", file_name, e, e);
                                failed_count += 1;
                            }
                        }
                    }
                    Err(e) => {
                        println!("kdf-init: failed to open {}: {}", file_name, e);
                        failed_count += 1;
                    }
                }
            }
        }
    }

    println!("kdf-init: module loading complete: {} loaded, {} failed, {} total", loaded_count, failed_count, total_count);

    Ok(())
}

pub fn shutdown() -> Result<()> {
    use rustix::system::reboot;
    use rustix::system::RebootCommand;

    println!("kdf-init: shutting down system");

    // Perform system shutdown
    reboot(RebootCommand::PowerOff)
        .context("Failed to shutdown system")?;

    Ok(())
}
