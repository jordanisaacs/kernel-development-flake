//! Kernel cmdline parser for kdf-init parameters

use anyhow::{Context, Result};
use std::collections::HashMap;

/// Virtiofs mount specification
#[derive(Debug, Clone, PartialEq)]
pub struct VirtiofsMount {
    /// Virtiofs tag to mount
    pub tag: String,
    /// Path to mount at
    pub path: String,
    /// Whether to create overlayfs with writable layer
    pub with_overlay: bool,
}

/// Symlink specification
#[derive(Debug, Clone, PartialEq)]
pub struct Symlink {
    /// Source path for symlink
    pub source: String,
    /// Target path to link to
    pub target: String,
}

/// Parse init.shell value by splitting on whitespace
///
/// Example: "sh -i" -> ("sh", vec!["-i"])
/// Example: "sh" -> ("sh", vec![])
fn parse_shell_command(value: &str) -> Result<(String, Vec<String>)> {
    let mut parts: Vec<String> = value.split_whitespace().map(|s| s.to_string()).collect();
    if parts.is_empty() {
        anyhow::bail!("Shell command is empty");
    }
    let program = parts.remove(0);
    Ok((program, parts))
}

/// Parse backtick-wrapped command value
///
/// Example: "`echo hello world`" -> "echo hello world"
fn parse_backtick_command(value: &str) -> Result<String> {
    // Command must be wrapped in backticks
    if !value.starts_with('`') || !value.ends_with('`') || value.len() < 2 {
        anyhow::bail!("Command value must be wrapped in backticks");
    }

    // Remove backticks and return the command string
    Ok(value[1..value.len() - 1].to_string())
}

/// Parsed init configuration from kernel cmdline
#[derive(Debug, PartialEq)]
pub struct Config {
    /// Virtiofs mounts to create
    pub virtiofs_mounts: Vec<VirtiofsMount>,
    /// Symlinks to create
    pub symlinks: Vec<Symlink>,
    /// Environment variables to set
    pub env_vars: HashMap<String, String>,
    /// Shell program and args - required (program, args)
    pub shell: (String, Vec<String>),
    /// Optional script to execute (not yet implemented)
    pub script: Option<String>,
    /// Directory to load kernel modules from (if None, no modules loaded)
    pub moddir: Option<String>,
    /// Console device to use - required
    pub console: String,
    /// Optional directory to change to before spawning shell
    pub chdir: Option<String>,
}

/// Parse kernel cmdline into Config
///
/// Supports: init.virtiofs, init.symlinks, init.env.XXX, init.shell, init.script, init.moddir, init.console, init.chdir
/// init.shell and init.script values must be wrapped in backticks
/// init.shell is required, init.script is optional
/// init.console is required
pub fn parse_cmdline(cmdline: &str) -> Result<Config> {
    let mut virtiofs_mounts = Vec::new();
    let mut symlinks = Vec::new();
    let mut env_vars = HashMap::new();
    let mut shell = None;
    let mut script = None;
    let mut moddir = None;
    let mut console = None;
    let mut chdir = None;

    // Parse parameters respecting backtick-enclosed values
    let params = parse_cmdline_params(cmdline);

    for param in params {
        if let Some(value) = param.strip_prefix("init.virtiofs=") {
            virtiofs_mounts = parse_virtiofs_mounts(value)?;
        } else if let Some(value) = param.strip_prefix("init.symlinks=") {
            symlinks = parse_symlinks(value)?;
        } else if let Some(rest) = param.strip_prefix("init.env.") {
            if let Some((key, value)) = rest.split_once('=') {
                env_vars.insert(key.to_string(), value.to_string());
            }
        } else if let Some(value) = param.strip_prefix("init.shell=") {
            // First unwrap backticks, then split on whitespace
            let shell_cmd = parse_backtick_command(value)?;
            shell = Some(parse_shell_command(&shell_cmd)?);
        } else if let Some(value) = param.strip_prefix("init.script=") {
            let script_cmd = parse_backtick_command(value)?;
            script = Some(script_cmd);
        } else if let Some(value) = param.strip_prefix("init.moddir=") {
            moddir = Some(value.to_string());
        } else if let Some(value) = param.strip_prefix("init.console=") {
            console = Some(value.to_string());
        } else if let Some(value) = param.strip_prefix("init.chdir=") {
            chdir = Some(value.to_string());
        }
    }

    // Ensure required fields are present
    let shell = shell.context("init.shell is required")?;
    let console = console.context("init.console is required")?;

    Ok(Config {
        virtiofs_mounts,
        symlinks,
        env_vars,
        shell,
        script,
        moddir,
        console,
        chdir,
    })
}

/// Parse cmdline parameters, handling backtick-enclosed values
fn parse_cmdline_params(cmdline: &str) -> Vec<String> {
    let mut params = Vec::new();
    let mut current_param = String::new();
    let mut in_backticks = false;
    let chars = cmdline.chars().peekable();

    for ch in chars {
        match ch {
            '`' => {
                in_backticks = !in_backticks;
                current_param.push(ch);
            }
            ' ' | '\t' | '\n' if !in_backticks => {
                if !current_param.is_empty() {
                    params.push(current_param.clone());
                    current_param.clear();
                }
            }
            _ => {
                current_param.push(ch);
            }
        }
    }

    if !current_param.is_empty() {
        params.push(current_param);
    }

    params
}

fn parse_virtiofs_mounts(value: &str) -> Result<Vec<VirtiofsMount>> {
    let mut mounts = Vec::new();

    for mount_spec in value.split(',') {
        if mount_spec.is_empty() {
            continue;
        }

        let parts: Vec<&str> = mount_spec.split(':').collect();

        let (tag, path, with_overlay) = match parts.as_slice() {
            [tag, path] => (*tag, *path, false),
            [tag, path, overlay] => (*tag, *path, *overlay == "Y"),
            _ => anyhow::bail!("Invalid virtiofs mount spec: {}", mount_spec),
        };

        mounts.push(VirtiofsMount {
            tag: tag.to_string(),
            path: path.to_string(),
            with_overlay,
        });
    }

    Ok(mounts)
}

fn parse_symlinks(value: &str) -> Result<Vec<Symlink>> {
    let mut symlinks = Vec::new();

    for symlink_spec in value.split(',') {
        if symlink_spec.is_empty() {
            continue;
        }

        let (source, target) = symlink_spec
            .split_once(':')
            .context(format!("Invalid symlink spec: {}", symlink_spec))?;

        symlinks.push(Symlink {
            source: source.to_string(),
            target: target.to_string(),
        });
    }

    Ok(symlinks)
}

/// Read kernel cmdline from /proc/cmdline
pub fn read_cmdline() -> Result<String> {
    std::fs::read_to_string("/proc/cmdline")
        .context("Failed to read /proc/cmdline")
        .map(|s| s.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_empty_cmdline() {
        let result = parse_cmdline("");
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("init.shell is required"));
    }

    #[test]
    fn test_parse_virtiofs_basic() {
        let config =
            parse_cmdline("init.console=console init.shell=`sh` init.virtiofs=share:/mnt/share")
                .unwrap();
        assert_eq!(config.virtiofs_mounts.len(), 1);
        assert_eq!(config.virtiofs_mounts[0].tag, "share");
        assert_eq!(config.virtiofs_mounts[0].path, "/mnt/share");
        assert!(!config.virtiofs_mounts[0].with_overlay);
    }

    #[test]
    fn test_parse_virtiofs_with_overlay() {
        let config =
            parse_cmdline("init.console=console init.shell=`sh` init.virtiofs=share:/mnt/share:Y")
                .unwrap();
        assert_eq!(config.virtiofs_mounts.len(), 1);
        assert!(config.virtiofs_mounts[0].with_overlay);
    }

    #[test]
    fn test_parse_virtiofs_multiple() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.virtiofs=share1:/mnt/a,share2:/mnt/b:Y",
        )
        .unwrap();
        assert_eq!(config.virtiofs_mounts.len(), 2);
        assert_eq!(config.virtiofs_mounts[0].tag, "share1");
        assert_eq!(config.virtiofs_mounts[0].path, "/mnt/a");
        assert!(!config.virtiofs_mounts[0].with_overlay);
        assert_eq!(config.virtiofs_mounts[1].tag, "share2");
        assert_eq!(config.virtiofs_mounts[1].path, "/mnt/b");
        assert!(config.virtiofs_mounts[1].with_overlay);
    }

    #[test]
    fn test_parse_symlinks() {
        let config =
            parse_cmdline("init.console=console init.shell=`sh` init.symlinks=/bin/sh:/bin/bash,/usr/bin/vi:/usr/bin/vim").unwrap();
        assert_eq!(config.symlinks.len(), 2);
        assert_eq!(config.symlinks[0].source, "/bin/sh");
        assert_eq!(config.symlinks[0].target, "/bin/bash");
        assert_eq!(config.symlinks[1].source, "/usr/bin/vi");
        assert_eq!(config.symlinks[1].target, "/usr/bin/vim");
    }

    #[test]
    fn test_parse_env_vars() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.env.PATH=/usr/bin init.env.HOME=/root",
        )
        .unwrap();
        assert_eq!(config.env_vars.len(), 2);
        assert_eq!(config.env_vars.get("PATH"), Some(&"/usr/bin".to_string()));
        assert_eq!(config.env_vars.get("HOME"), Some(&"/root".to_string()));
    }

    #[test]
    fn test_parse_shell() {
        let config = parse_cmdline("init.console=console init.shell=`/bin/sh`").unwrap();
        assert_eq!(config.shell, ("/bin/sh".to_string(), vec![]));
        assert_eq!(config.console, "console");
        assert_eq!(config.chdir, None);
    }

    #[test]
    fn test_parse_chdir() {
        let config =
            parse_cmdline("init.console=console init.shell=`sh` init.chdir=/mnt/workdir").unwrap();
        assert_eq!(config.chdir, Some("/mnt/workdir".to_string()));
    }

    #[test]
    fn test_parse_shell_with_args() {
        let config = parse_cmdline("init.console=console init.shell=`sh -i`").unwrap();
        assert_eq!(config.shell, ("sh".to_string(), vec!["-i".to_string()]));
        assert_eq!(config.console, "console");
    }

    #[test]
    fn test_parse_script() {
        let config =
            parse_cmdline("init.console=console init.shell=`sh` init.script=`/bin/echo hello`")
                .unwrap();
        assert_eq!(config.script, Some("/bin/echo hello".to_string()));
        assert_eq!(config.console, "console");
    }

    #[test]
    fn test_parse_full_cmdline() {
        let cmdline = "console=ttyS0 init.console=ttyS0 init.virtiofs=share:/mnt:Y init.symlinks=/bin/sh:/bin/bash init.env.PATH=/usr/bin init.shell=`/bin/sh` quiet";
        let config = parse_cmdline(cmdline).unwrap();

        assert_eq!(config.virtiofs_mounts.len(), 1);
        assert_eq!(config.virtiofs_mounts[0].tag, "share");
        assert_eq!(config.virtiofs_mounts[0].path, "/mnt");
        assert!(config.virtiofs_mounts[0].with_overlay);

        assert_eq!(config.symlinks.len(), 1);
        assert_eq!(config.symlinks[0].source, "/bin/sh");
        assert_eq!(config.symlinks[0].target, "/bin/bash");

        assert_eq!(config.env_vars.get("PATH"), Some(&"/usr/bin".to_string()));
        assert_eq!(config.shell, ("/bin/sh".to_string(), vec![]));
        assert_eq!(config.console, "ttyS0");
    }

    #[test]
    fn test_parse_invalid_virtiofs() {
        let result = parse_cmdline("init.virtiofs=invalid");
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_invalid_symlink() {
        let result = parse_cmdline("init.symlinks=invalid");
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_command_with_backticks() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.script=`/bin/echo hello world`",
        )
        .unwrap();
        assert_eq!(config.script, Some("/bin/echo hello world".to_string()));
    }

    #[test]
    fn test_parse_command_with_backticks_and_args() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.script=`/usr/bin/ls -la /tmp`",
        )
        .unwrap();
        assert_eq!(config.script, Some("/usr/bin/ls -la /tmp".to_string()));
    }

    #[test]
    fn test_parse_command_without_backticks() {
        let result = parse_cmdline("init.console=console init.shell=`sh` init.script=/bin/sh");
        assert!(result.is_err());
        assert!(result
            .unwrap_err()
            .to_string()
            .contains("must be wrapped in backticks"));
    }

    #[test]
    fn test_parse_command_with_backticks_in_full_cmdline() {
        let cmdline =
            "console=ttyS0 init.console=ttyS0 init.shell=`sh` init.env.PATH=/usr/bin init.script=`/bin/echo hello world` quiet";
        let config = parse_cmdline(cmdline).unwrap();
        assert_eq!(config.script, Some("/bin/echo hello world".to_string()));
        assert_eq!(config.env_vars.get("PATH"), Some(&"/usr/bin".to_string()));
    }

    #[test]
    fn test_parse_command_with_multiple_spaces() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.script=`/bin/echo   multiple   spaces`",
        )
        .unwrap();
        assert_eq!(
            config.script,
            Some("/bin/echo   multiple   spaces".to_string())
        );
    }

    #[test]
    fn test_parse_backticked_command_with_special_chars() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.script=`/bin/sh -c \"echo test\"`",
        )
        .unwrap();
        assert_eq!(config.script, Some("/bin/sh -c \"echo test\"".to_string()));
    }

    #[test]
    fn test_parse_empty_backticked_command() {
        let config = parse_cmdline("init.console=console init.shell=`sh` init.script=``").unwrap();
        assert_eq!(config.script, Some("".to_string()));
    }

    #[test]
    fn test_parse_cmdline_with_all_features_and_backticks() {
        let cmdline = "console=ttyS0 init.console=ttyS0 init.virtiofs=share:/mnt:Y init.symlinks=/bin/sh:/bin/bash init.env.PATH=/usr/bin init.env.HOME=/root init.shell=`sh` init.script=`/bin/echo test 1 2 3` init.moddir=/lib/modules quiet";
        let config = parse_cmdline(cmdline).unwrap();

        assert_eq!(config.virtiofs_mounts.len(), 1);
        assert_eq!(config.virtiofs_mounts[0].tag, "share");
        assert_eq!(config.virtiofs_mounts[0].path, "/mnt");
        assert!(config.virtiofs_mounts[0].with_overlay);

        assert_eq!(config.symlinks.len(), 1);
        assert_eq!(config.symlinks[0].source, "/bin/sh");
        assert_eq!(config.symlinks[0].target, "/bin/bash");

        assert_eq!(config.env_vars.len(), 2);
        assert_eq!(config.env_vars.get("PATH"), Some(&"/usr/bin".to_string()));
        assert_eq!(config.env_vars.get("HOME"), Some(&"/root".to_string()));

        assert_eq!(config.shell, ("sh".to_string(), vec![]));
        assert_eq!(config.script, Some("/bin/echo test 1 2 3".to_string()));

        assert_eq!(config.moddir, Some("/lib/modules".to_string()));
        assert_eq!(config.console, "ttyS0");
    }

    #[test]
    fn test_parse_cmdline_params_basic() {
        let params = parse_cmdline_params("foo bar baz");
        assert_eq!(params, vec!["foo", "bar", "baz"]);
    }

    #[test]
    fn test_parse_cmdline_params_with_backticks() {
        let params = parse_cmdline_params("foo init.script=`hello world` bar");
        assert_eq!(params, vec!["foo", "init.script=`hello world`", "bar"]);
    }

    #[test]
    fn test_parse_cmdline_params_multiple_backticks() {
        let params = parse_cmdline_params("init.script=`echo test` init.env.X=`value with spaces`");
        assert_eq!(
            params,
            vec!["init.script=`echo test`", "init.env.X=`value with spaces`"]
        );
    }

    #[test]
    fn test_parse_cmdline_params_tabs_and_newlines() {
        let params = parse_cmdline_params("foo\tbar\nbaz");
        assert_eq!(params, vec!["foo", "bar", "baz"]);
    }

    #[test]
    fn test_parse_command_with_equals_in_backticks() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.script=`/bin/env KEY=VALUE ls`",
        )
        .unwrap();
        assert_eq!(config.script, Some("/bin/env KEY=VALUE ls".to_string()));
    }

    #[test]
    fn test_parse_command_with_multiple_equals_in_backticks() {
        let config = parse_cmdline(
            "init.console=console init.shell=`sh` init.script=`FOO=bar BAZ=qux /bin/test`",
        )
        .unwrap();
        assert_eq!(config.script, Some("FOO=bar BAZ=qux /bin/test".to_string()));
    }

    #[test]
    fn test_parse_full_cmdline_with_equals_in_command() {
        let cmdline =
            "console=ttyS0 init.console=ttyS0 init.shell=`sh` init.env.PATH=/usr/bin init.shell=`sh` init.script=`/bin/env TEST=123 ls -la` quiet";
        let config = parse_cmdline(cmdline).unwrap();
        assert_eq!(config.script, Some("/bin/env TEST=123 ls -la".to_string()));
        assert_eq!(config.env_vars.get("PATH"), Some(&"/usr/bin".to_string()));
    }

    #[test]
    fn test_parse_shell_and_script_together() {
        let config =
            parse_cmdline("init.console=console init.shell=`/bin/sh` init.script=`/bin/ls`")
                .unwrap();
        assert_eq!(config.shell, ("/bin/sh".to_string(), vec![]));
        assert_eq!(config.script, Some("/bin/ls".to_string()));
    }
}
