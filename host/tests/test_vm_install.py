import json
import socket
import threading

from vasistas import vm


def test_fin_d_installation_lue_sur_qmp(tmp_path, monkeypatch):
    path = tmp_path / "qmp-host.sock"
    monkeypatch.setattr(vm, "QMP_HOST", path)
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(1)

    def qemu():
        c, _ = srv.accept()
        f = c.makefile("rwb")
        f.write(b'{"QMP": {}}\n')
        f.flush()
        f.readline()  # qmp_capabilities
        for line in ({"return": {}}, {"event": "RESET", "data": {"reason": "guest-reset"}},
                     {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-shutdown"}}):
            f.write(json.dumps(line).encode() + b"\n")
        f.flush()
        f.close()
        c.close()
    threading.Thread(target=qemu, daemon=True).start()
    reasons = []
    vm._watch_shutdown(reasons)
    assert reasons == ["guest-shutdown"]
