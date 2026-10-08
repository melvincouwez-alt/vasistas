#!/bin/sh
# Compile viogpudo (pilote d'affichage virtio-gpu de virtio-win) avec le correctif vsync.patch,
# sous Linux : clang-cl + lld-link, en-têtes MSVC/SDK de xwin, WDK du paquet NuGet officiel.
# Préparer une fois ~/.local/opt/wdk-cross (llvm/, xwin/, wdk/, src/ = clone virtio-win).
# Sortie : out/viogpudo.sys (non signé, à signer dans l'invité).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
X=${WDK_CROSS:-$HOME/.local/opt/wdk-cross}
SRC=$X/src
OUT=$HERE/out
W=$X/wdk/c/Include/10.0.26100.0
WL=$X/wdk/c/Lib/10.0.26100.0/km/x64
CC=$X/llvm/bin/clang-cl
mkdir -p "$OUT/obj" "$OUT/tmh"
(cd "$SRC" && git checkout -q -- viogpu && { [ "$NOPATCH" = 1 ] || git apply "$HERE/vsync.patch"; })
# traçage WPP (tracewpp n'existe pas sous Linux) : les .tmh deviennent des macros vides
for f in driver viogpudo bitops viogpu_idr viogpu_pci viogpu_queue; do
  printf '#undef DbgPrint\n#define DbgPrint(l, m) ((void)0)\n#define WPP_INIT_TRACING(d, r)\n#define WPP_CLEANUP(d)\n' > "$OUT/tmh/$f.tmh"
done
# Windows ignore la casse des #include : liens en minuscules vers chaque en-tête
mkdir -p "$OUT/case"
for d in "$SRC/VirtIO" "$SRC/viogpu/common" "$SRC/viogpu/shared" "$SRC/viogpu/viogpudo" "$W/km" "$W/shared"; do
  for h in "$d"/*.h; do
    l=$(basename "$h" | tr 'A-Z' 'a-z'); [ -e "$OUT/case/$l" ] || ln -s "$h" "$OUT/case/$l"
  done
done
FLAGS="$EXTRA --target=x86_64-pc-windows-msvc /kernel /c /O2 /GS- /GR- /Gy /Zc:wchar_t /W0 -Wno-everything
 -fms-compatibility -fms-extensions -fdelayed-template-parsing
 /D_AMD64_ /DAMD64 /D_WIN64 /D_KERNEL_MODE /DNTDDI_VERSION=0x0A000000 /D_WIN32_WINNT=0x0A00 /DWINVER=0x0A00
 /D_NO_CRT_STDIO_INLINE /DPOOL_NX_OPTIN=1 /DDBG=0 /DVIOGPU_DOD=1
 /I$OUT/tmh /I$SRC/viogpu/common /I$SRC/viogpu/shared /I$SRC/VirtIO /I$SRC/viogpu/viogpudo
 /I$W/km /I$W/shared /I$OUT/case /imsvc$X/xwin/sdk/include/shared /imsvc$X/xwin/sdk/include/um /imsvc$X/xwin/crt/include /imsvc$X/xwin/sdk/include/ucrt"
for f in VirtIO/VirtIOPCICommon.c VirtIO/VirtIOPCILegacy.c VirtIO/VirtIOPCIModern.c VirtIO/VirtIORing.c \
         VirtIO/VirtIORing-Packed.c viogpu/common/edid.cpp viogpu/common/viogpu_idr.cpp \
         viogpu/common/viogpu_pci.cpp viogpu/common/viogpu_queue.cpp viogpu/common/baseobj.cpp \
         viogpu/common/bitops.cpp viogpu/viogpudo/driver.cpp viogpu/viogpudo/viogpudo.cpp; do
  $CC $FLAGS "/Fo$OUT/obj/$(basename "${f%.*}").obj" "$SRC/$f"
done
$CC $FLAGS "/Fo$OUT/obj/portio.obj" "$HERE/portio.c"
# llvm-rc ne lit pas l'UTF-16 du .rc d'origine
RC=$SRC/viogpu/viogpudo/viogpudo.utf8.rc
iconv -f UTF-16 -t UTF-8 "$SRC/viogpu/viogpudo/viogpudo.rc" | sed -e '1s/^\xEF\xBB\xBF//' -e '/#include/s|\\|/|g' > "$RC"
$X/llvm/bin/llvm-rc /I "$SRC/viogpu/viogpudo" /I "$OUT/case" /I "$W/km" /I "$W/shared" /I "$X/xwin/sdk/include/shared" /I "$X/xwin/sdk/include/um" \
  /fo "$OUT/obj/viogpudo.res" "$RC" || echo "ressources ignorées"
$X/llvm/bin/lld-link /DRIVER /SUBSYSTEM:NATIVE,10.0 /ENTRY:GsDriverEntry /NODEFAULTLIB /MACHINE:X64 \
  /INCREMENTAL:NO /OPT:REF /TSAWARE:NO /SECTION:INIT,ERD /SECTION:.text,ERP /SECTION:.rdata,RP /SECTION:.data,RWP /SECTION:.pdata,RP /RELEASE /OUT:"$OUT/viogpudo.sys" "$OUT"/obj/*.obj \
  $( [ -f "$OUT/obj/viogpudo.res" ] && echo "$OUT/obj/viogpudo.res" ) \
  "$WL/ntoskrnl.lib" "$WL/hal.lib" "$WL/wmilib.lib" "$WL/displib.lib" "$WL/bufferoverflowfastfailk.lib"
ls -la "$OUT/viogpudo.sys"
