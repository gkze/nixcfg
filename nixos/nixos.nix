{ src, pkgs, ... }@args:
import "${src}/lib/mksystem.nix" (
  args
  // {
    systemModules = [
      (
        { modulesPath, ... }:
        {
          imports = [ "${modulesPath}/virtualisation/qemu-vm.nix" ];

          services = {
            spice-vdagent.enable = true;
            spice-webdavd.enable = true;
            qemuGuest.enable = true;
          };

          virtualisation = {
            libvirtd.qemu.vhostUserPackages = [ pkgs.virtiofsd ];

            qemu.options = [
              "-device virtio-vga"
              "-m 2048"
            ];
          };
        }
      )
    ];
  }
)
