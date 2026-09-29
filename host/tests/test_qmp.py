"""Fil QMP de l'hôte : sérialisation, erreurs de commande, reconnexion."""

import json
import socket
import threading

import pytest

from vasistas import vm


class FakeQemu:
    """Moniteur QMP minimal : répond « return » avec le nom de la commande."""

    def __init__(self, path):
        self.path = path
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(path))
        self.srv.listen(4)
        self.clients = 0
        self.commands = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.clients += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        f = conn.makefile("rwb")
        f.write(b'{"QMP": {}}\n')
        f.flush()
        for line in f:
            req = json.loads(line)
            name = req["execute"]
            self.commands.append(name)
            if name == "boom":
                reply = {"error": {"desc": "boom"}}
            elif name == "hangup":
                conn.close()
                return
            else:
                reply = {"return": {"name": name, "args": req.get("arguments")}}
            f.write(json.dumps(reply).encode() + b"\n")
            f.flush()


@pytest.fixture
def qemu(tmp_path, monkeypatch):
    monkeypatch.setattr(vm, "QMP", tmp_path / "qmp.sock")
    monkeypatch.setattr(vm, "QMP_HOST", tmp_path / "qmp-host.sock")
    return FakeQemu(tmp_path / "qmp-host.sock")


def test_connexion_persistante(qemu):
    w = vm.QmpWorker()
    for i in range(5):
        assert w.call("query-status")["name"] == "query-status"
    assert qemu.clients == 1
    assert qemu.commands == ["qmp_capabilities"] + ["query-status"] * 5


def test_argument_name(qemu):
    w = vm.QmpWorker()
    assert w.call("qom-get", path="/x", property="y")["args"] == {"path": "/x", "property": "y"}


def test_erreur_de_commande_garde_la_connexion(qemu):
    w = vm.QmpWorker()
    with pytest.raises(RuntimeError):
        w.call("boom")
    assert w.call("ok")["name"] == "ok"
    assert qemu.clients == 1


def test_reconnexion_apres_coupure(qemu):
    w = vm.QmpWorker()
    w.call("ok")
    fut = w.submit(lambda q: q.execute("hangup"))
    with pytest.raises((EOFError, OSError)):
        fut.result(5)
    assert w.call("ok")["name"] == "ok"
    assert qemu.clients >= 2


def test_asynchrone_signale_l_echec(qemu):
    w = vm.QmpWorker()
    got = threading.Event()
    w.call_async("boom", on_error=lambda e: got.set())
    assert got.wait(5)


def test_ligne_de_commande_qemu():
    args = vm.qemu_args(False)
    joined = " ".join(args)
    assert "hpet=off" in joined and "iothread=io0" in joined
    assert f"unix:{vm.QMP_HOST},server=on,wait=off" in args
