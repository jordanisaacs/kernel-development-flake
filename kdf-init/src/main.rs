//! kdf-init: minimal Rust init for initramfs with virtiofs and overlayfs support

mod cmdline;
mod system;
mod virtiofs;

use anyhow::Result;

fn main() -> Result<()> {
    // Run main logic and always shutdown, even on error
    if let Err(e) = run() {
        eprintln!("kdf-init: fatal error: {:?}", e);
        let _ = system::shutdown();
        return Err(e);
    }
    Ok(())
}

fn run() -> Result<()> {
    println!("kdf-init: starting minimal Rust init");

    // Mount kernel filesystems
    system::mount_kernel_filesystems()?;

    // Parse kernel cmdline
    let cmdline_str = cmdline::read_cmdline()?;
    println!("kdf-init: kernel cmdline: {}", cmdline_str);

    let config = cmdline::parse_cmdline(&cmdline_str)?;

    println!("kdf-init: parsed configuration:");
    println!("  virtiofs mounts: {}", config.virtiofs_mounts.len());
    println!("  symlinks: {}", config.symlinks.len());
    println!("  env vars: {}", config.env_vars.len());
    println!("  shell: {:?}", config.shell);
    println!("  script: {:?}", config.script);

    // Load kernel modules from configured directory
    system::load_kernel_modules(config.moddir.as_deref())?;

    // Mount virtiofs shares with optional overlayfs
    virtiofs::mount_virtiofs_shares(&config.virtiofs_mounts)?;

    // TODO: Create symlinks

    // Set environment variables
    for (key, value) in &config.env_vars {
        println!("kdf-init: setting env var: {}={}", key, value);
        std::env::set_var(key, value);
    }

    // Change directory if specified
    if let Some(chdir) = &config.chdir {
        println!("kdf-init: changing directory to: {}", chdir);
        std::env::set_current_dir(chdir)?;
    }

    // Execute shell
    let (program, args) = &config.shell;
    let display_cmd = if args.is_empty() {
        program.to_string()
    } else {
        format!("{} {}", program, args.join(" "))
    };
    println!("kdf-init: starting interactive shell: {}", display_cmd);

    let exit_status = system::execute_shell(program, args, &config.console)?;

    if exit_status.success() {
        println!("kdf-init: shell exited successfully");
    } else {
        eprintln!(
            "kdf-init: shell exited with status: {:?}",
            exit_status.code()
        );
    }

    // TODO: Handle optional script execution
    if config.script.is_some() {
        eprintln!("kdf-init: init.script is not yet implemented");
    }

    println!("kdf-init: initialization complete");

    // Shutdown the system
    system::shutdown()?;

    Ok(())
}
