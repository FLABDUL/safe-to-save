"""Boundary checks for Cloud development database ownership."""

import unittest

import db


class CloudDatabaseBoundaryTest(unittest.TestCase):
    def test_port_probe_rejects_listener_and_allows_recent_connections(self):
        import socket
        from unittest.mock import patch

        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with patch.object(db, "PORT", port), self.assertRaises(OSError):
                db.unused_port()
            with socket.create_connection(("127.0.0.1", port)) as client:
                connection, _ = server.accept()
                connection.shutdown(socket.SHUT_RDWR)
                connection.close()
                client.recv(1)
        with patch.object(db, "PORT", port):
            db.unused_port()

    def config(self):
        return {
            "project": db.PROJECT,
            "root": str(db.ROOT),
            "port": db.PORT,
            "database": db.DATABASE,
            "user": db.ADMIN,
            "synthetic_only": True,
            "password": "a" * 64,
        }

    def test_rejects_foreign_project_port_database_or_checkout(self):
        for key, value in (
            ("project", "foreign"),
            ("root", "/another/checkout"),
            ("port", 5432),
            ("database", "production"),
            ("synthetic_only", False),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                db.validate_config({**self.config(), key: value})

    def test_inherited_libpq_routing_cannot_redirect_a_connection(self):
        from unittest.mock import patch

        with patch.dict(
            db.os.environ,
            {
                "PGHOSTADDR": "remote",
                "PGSERVICE": "live",
                "PGOPTIONS": "unsafe",
                "PGPASSWORD": "inherited",
            },
        ):
            env = db.pg_env(self.config())
        self.assertEqual(env["PGHOSTADDR"], "127.0.0.1")
        self.assertEqual(env["PGPASSWORD"], "a" * 64)
        self.assertNotIn("PGSERVICE", env)
        self.assertNotIn("PGOPTIONS", env)

    def test_rejects_a_symlinked_state_directory(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.symlink_to(root, target_is_directory=True)
            with (
                patch.object(db, "ROOT", root),
                patch.object(db, "STATE", state),
                self.assertRaises(ValueError),
            ):
                db.checked_paths()
