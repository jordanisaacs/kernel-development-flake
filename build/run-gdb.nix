{
  lib,
  writeScriptBin,
  runCommand,
  gdb,
}:
{
  kernel,
  modules ? [ ],
}:
let
  moduleDirs = builtins.map (m: "${m}/lib/modules/${kernel.modDirVersion}/misc") modules;

  moduleSourceDirs = builtins.map (m: m.src) modules;

  symbolsDirs = builtins.concatStringsSep " " moduleDirs;

  searchDirs = builtins.concatStringsSep " " (builtins.map (m: ''-ex "dir ${m}"'') moduleSourceDirs);

  version = builtins.head (builtins.split "-" kernel.modDirVersion);
in
# Need a to go a directroy above source (eg. scripts) because vmlinux is using relative path
writeScriptBin "rungdb" ''
  ${gdb}/bin/gdb \
    ${searchDirs} \
    -ex "dir ${kernel.dev}/lib/modules/${kernel.modDirVersion}/source/scripts" \
    -ex "file ${kernel.dev}/vmlinux" \
    -ex "source ${kernel.dev}/lib/modules/${kernel.modDirVersion}/build/vmlinux-gdb.py" \
    -ex "alias lx-symbols-nix = lx-symbols ${symbolsDirs}" \
    -ex "target remote localhost:1234"
''
