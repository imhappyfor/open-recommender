import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from open_recommender.crypto import write_private_file


class PrivateFileTests(unittest.TestCase):
    def test_failed_flush_or_commit_preserves_previous_bytes_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "owner.orf"
            write_private_file(target, b"original")
            for operation in ("fsync", "replace"):
                with self.subTest(operation=operation), patch(
                    "open_recommender.crypto.os." + operation, side_effect=OSError("simulated disk failure")
                ), self.assertRaises(OSError):
                    write_private_file(target, b"replacement")
                self.assertEqual(target.read_bytes(), b"original")
                self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_no_clobber_remains_exclusive_when_another_file_appears_at_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "owner.key"
            link = os.link

            def concurrent_create(source, destination):
                Path(destination).write_bytes(b"another writer")
                link(source, destination)

            with patch("open_recommender.crypto.os.link", side_effect=concurrent_create), self.assertRaises(FileExistsError):
                write_private_file(target, b"new secret", overwrite=False)
            self.assertEqual(target.read_bytes(), b"another writer")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    @unittest.skipUnless(os.name == "posix", "Unix permission bits")
    def test_replacement_and_staged_bytes_are_owner_only_even_with_permissive_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "owner.key"
            target.write_bytes(b"original")
            target.chmod(0o644)
            replace = os.replace

            def checked_replace(source, destination):
                self.assertEqual(Path(source).stat().st_mode & 0o777, 0o600)
                self.assertEqual(target.read_bytes(), b"original")
                replace(source, destination)

            previous_umask = os.umask(0)
            try:
                with patch("open_recommender.crypto.os.replace", side_effect=checked_replace):
                    write_private_file(target, b"replacement")
            finally:
                os.umask(previous_umask)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.read_bytes(), b"replacement")

    @unittest.skipUnless(os.name == "posix", "Unix symlinks")
    def test_symlink_destination_is_not_followed_or_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "original"
            target.write_bytes(b"original")
            alias = Path(directory) / "alias.key"
            alias.symlink_to(target)
            with self.assertRaises(ValueError):
                write_private_file(alias, b"new secret")
            self.assertTrue(alias.is_symlink())
            self.assertEqual(target.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
