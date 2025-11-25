{
  pkgs,
  lib,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
}:

let
  # Load the workspace from uv.lock
  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

  # Create the overlay
  overlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };

  # Build Python set with overlay
  pythonSet = (pkgs.callPackage pyproject-nix.build.packages {
    python = pkgs.python3;
  }).overrideScope (
    lib.composeManyExtensions [
      pyproject-build-systems.overlays.default
      overlay
    ]
  );

  # Get mkApplication utility
  inherit (pkgs.callPackage pyproject-nix.build.util { }) mkApplication;

  # Build the virtual environment with the package
  venv = pythonSet.mkVirtualEnv "kdf-cli-env" workspace.deps.default;

  # Create the application
  app = mkApplication {
    inherit venv;
    package = pythonSet.kdf-cli;
  };
in
# Wrap with runtime dependencies
pkgs.runCommand "kdf-cli"
  {
    nativeBuildInputs = [ pkgs.makeWrapper ];
    meta = app.meta // {
      description = "Kernel development flake - Manage kdf-init initramfs and kernel execution";
      license = lib.licenses.mit;
      mainProgram = "kdf";
    };
  }
  ''
    mkdir -p $out/bin
    cp -r ${app}/* $out/

    # Wrap the binary with runtime dependencies
    wrapProgram $out/bin/kdf \
      --prefix PATH : ${lib.makeBinPath [
        pkgs.qemu
        pkgs.virtiofsd
        pkgs.coreutils
        pkgs.xz
        pkgs.gzip
        pkgs.cpio
        pkgs.findutils
        pkgs.kmod
      ]}
  ''
