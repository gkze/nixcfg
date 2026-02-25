{ inputs, prev, ... }:
{
  ladybird = prev.ladybird.overrideAttrs (
    old:
    let
      isDarwin = prev.stdenv.hostPlatform.isDarwin;

      patchedLibtommath = prev.libtommath.overrideAttrs (libOld: {
        patches = (libOld.patches or [ ]) ++ [
          "${inputs.ladybird}/Meta/CMake/vcpkg/overlay-ports/libtommath/has-set-double.patch"
          "${inputs.ladybird}/Meta/CMake/vcpkg/overlay-ports/libtommath/stdc-iec-559.patch"
        ];
      });

      replaceLibtommath =
        deps:
        (builtins.filter (dep: (dep.pname or (dep.name or "")) != "libtommath") deps)
        ++ [ patchedLibtommath ];
    in
    {
      version = "unstable-${inputs.ladybird.shortRev or (builtins.substring 0 8 inputs.ladybird.rev)}";
      src = inputs.ladybird;

      postPatch = (old.postPatch or "") + ''
        substituteInPlace Libraries/LibWeb/WebGL/OpenGLContext.cpp \
          --replace-fail '    eglWaitUntilWorkScheduledANGLE(m_impl->display);' '    glFinish();'
      '';

      cmakeFlags =
        (old.cmakeFlags or [ ])
        ++ prev.lib.optionals isDarwin [
          (prev.lib.cmakeBool "ENABLE_QT" true)
          (prev.lib.cmakeBool "ENABLE_RUST" false)
        ];

      nativeBuildInputs =
        replaceLibtommath (old.nativeBuildInputs or [ ])
        ++ [ prev.python3 ]
        ++ prev.lib.optionals isDarwin [
          prev.git
        ];

      buildInputs =
        (old.buildInputs or [ ])
        ++ prev.lib.optionals isDarwin [
          prev.fmt
        ];

      dontWrapQtApps = false;
      postInstall = if isDarwin then "" else (old.postInstall or "");

      env =
        (old.env or { })
        // prev.lib.optionalAttrs isDarwin {
          NIX_LDFLAGS = "";
        };

      postFixup =
        (old.postFixup or "")
        + prev.lib.optionalString isDarwin ''
          rewrite_angle_libs() {
            local target="$1"
            install_name_tool -change ./libEGL.dylib ${prev.angle}/lib/libEGL.dylib "$target"
            install_name_tool -change ./libEGL_vulkan_secondaries.dylib ${prev.angle}/lib/libEGL_vulkan_secondaries.dylib "$target"
            install_name_tool -change ./libGLESv1_CM.dylib ${prev.angle}/lib/libGLESv1_CM.dylib "$target"
            install_name_tool -change ./libGLESv2.dylib ${prev.angle}/lib/libGLESv2.dylib "$target"
            install_name_tool -change ./libGLESv2_vulkan_secondaries.dylib ${prev.angle}/lib/libGLESv2_vulkan_secondaries.dylib "$target"
            install_name_tool -change ./libGLESv2_with_capture.dylib ${prev.angle}/lib/libGLESv2_with_capture.dylib "$target"
            install_name_tool -change ./libVkICD_mock_icd.dylib ${prev.angle}/lib/libVkICD_mock_icd.dylib "$target"
            install_name_tool -change @rpath/libfeature_support.dylib ${prev.angle}/lib/libfeature_support.dylib "$target"
          }

          for target in \
            "$out/lib/liblagom-web.0.0.0.dylib" \
            "$out/bundle/Ladybird.app/Contents/lib/liblagom-web.0.0.0.dylib"; do
            if [ -f "$target" ]; then
              chmod u+w "$target"
              rewrite_angle_libs "$target"
            fi
          done
        '';

      meta =
        (old.meta or { })
        // prev.lib.optionalAttrs isDarwin {
          broken = false;
        };
    }
  );
}
