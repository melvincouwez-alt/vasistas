# Composants tiers

Vasistas est publié sous licence MIT (voir [LICENSE](LICENSE)). Il contient ou modifie les
logiciels tiers suivants, chacun sous sa propre licence. Les textes des licences sont
reproduits plus bas dans leur langue d'origine (l'anglais), qui seule fait foi.

| Composant | Emplacement | Licence |
|---|---|---|
| [zstd](https://github.com/facebook/zstd) 1.5.7, version Windows officielle (`libzstd.dll`) | `guest/Vasistas.Agent/native/libzstd.dll.gz`, intégré à `Vasistas.Agent.exe` | BSD 3 clauses |
| [Interop.UIAutomationClient](https://github.com/Roemer/Interop.UIAutomationClient) 10.19041.0 | types d'interopérabilité intégrés à `Vasistas.Agent.exe` | MIT |
| [pycdlib](https://github.com/clalancette/pycdlib) 1.21.0 | `host/vendor`, sans modification | LGPL 2.1 (`host/vendor/pycdlib-1.21.0.dist-info/licenses/COPYING`) |
| Correctif de [QEMU](https://www.qemu.org) | `patches/qemu-11.1.1-pixman-udmabuf.patch` | GPL 2.0 ou ultérieure, comme QEMU |
| Correctif de viogpudo, tiré de [virtio-win](https://github.com/virtio-win/kvm-guest-drivers-windows) | `guest/viogpudo/vsync.patch` | BSD 3 clauses, comme virtio-win |

Vasistas ne contient aucun logiciel de Microsoft. Les images d'installation de Windows,
Office, les pilotes virtio-win (projet Fedora) et les applications installées par winget sont
téléchargés depuis leurs sources officielles au moment de l'installation et restent soumis à
leurs propres licences. La capture `docs/screenshot.png` montre Microsoft Word avec un
document fictif.

## zstd

```
BSD License

For Zstandard software

Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

 * Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

 * Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

 * Neither the name Facebook, nor Meta, nor the names of its contributors may
   be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Interop.UIAutomationClient

```
MIT License

Copyright (c) 2019 Roman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## virtio-win (viogpudo)

```
Copyright 2009-2025 Red Hat, Inc. and/or its affiliates.
Copyright 2016 Google, Inc.
Copyright 2016 Virtuozzo, Inc.
Copyright 2007 IBM Corporation

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

Neither the name of the copyright holder nor the names of its
contributors may be used to endorse or promote products derived from
this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
