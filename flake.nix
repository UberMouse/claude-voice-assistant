{
  description = "Claude voice assistant";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";
  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let pkgs = import nixpkgs { inherit system; config.allowUnfree = true; };
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python312
            uv
            ffmpeg
            portaudio
            pulseaudio
            pkg-config
          ];
          # pynput pulls in evdev on Linux, whose C extension needs <linux/input.h>.
          # uv/pip run outside Nix's build sandbox, so we expose the headers via CPATH below.
          nativeBuildInputs = with pkgs; [ linuxHeaders ];
          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.portaudio}/lib:${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
            export CPATH="${pkgs.linuxHeaders}/include:$CPATH"
          '';
        };
      });
}
