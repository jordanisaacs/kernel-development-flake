{
  pkgs,
  lib,
  ...
}:
{
  nixpkgs,
  structuredExtraConfig,
}:
rec {
  module = import "${nixpkgs}/nixos/modules/system/boot/kernel_config.nix";
  # used also in apache
  # { modules = [ { options = res.options; config = svc.config or svc; } ];
  #   check = false;
  # The result is a set of two attributes
  moduleStructuredConfig =
    (lib.evalModules {
      modules = [
        module
        {
          settings = structuredExtraConfig;
          _file = "structuredExtraConfig";
        }
      ];
    }).config;

  structuredConfig = moduleStructuredConfig.settings;
}
