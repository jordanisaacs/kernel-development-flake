//! kdf-init: minimal Rust init for initramfs with virtiofs and overlayfs support

mod cmdline;
mod system;
mod virtiofs;

use anyhow::Result;

fn main() -> Result<()> {
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
    println!("  command: {:?}", config.command);

    // Load kernel modules from configured directory
    system::load_kernel_modules(config.moddir.as_deref())?;

    // Mount virtiofs shares with optional overlayfs
    virtiofs::mount_virtiofs_shares(&config.virtiofs_mounts)?;

    // TODO: Create symlinks
    // TODO: Set environment variables
    // TODO: Execute command

    println!("kdf-init: initialization complete (stub)");

    // Shutdown the system
    system::shutdown()?;

    Ok(())
}
