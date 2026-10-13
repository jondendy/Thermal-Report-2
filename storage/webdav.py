"""WebDAV backend (Nextcloud, ownCloud, Synology, Apache mod_dav, NAS boxes...).

Implemented directly on ``requests`` (PROPFIND / MKCOL / PUT / GET / DELETE) so there
is no dependency on a particular WebDAV client library.
"""
from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET
from typing import List
from urllib.parse import quote, unquote, urlparse

import requests

from .base import StorageBackend, StorageError, StorageNotFound, clean_path

_DAV = "{DAV:}"


class WebDAVStorage(StorageBackend):
    def __init__(self, base_url: str, username: str = "", password: str = "",
                 verify_tls: bool = True, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if username:
            self.session.auth = (username, password)
        self.session.verify = verify_tls
        self.timeout = timeout
        self._known_dirs = set()

    def _url(self, path: str) -> str:
        rel = clean_path(path)
        return self.base_url + ("/" + quote(rel) if rel else "")

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        try:
            return self.session.request(method, self._url(path), timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise StorageError(f"WebDAV {method} {path} failed: {exc}") from exc

    def _propfind(self, path: str, depth: int):
        resp = self._request(
            "PROPFIND", path,
            headers={"Depth": str(depth), "Content-Type": "application/xml"},
            data=('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                  "<d:resourcetype/></d:prop></d:propfind>"),
        )
        if resp.status_code == 404:
            return None
        if resp.status_code not in (200, 207):
            raise StorageError(f"WebDAV PROPFIND {path} -> HTTP {resp.status_code}")
        return ET.fromstring(resp.content)

    def exists(self, path: str) -> bool:
        return self._propfind(path, 0) is not None

    def is_dir(self, path: str) -> bool:
        tree = self._propfind(path, 0)
        if tree is None:
            return False
        return tree.find(f".//{_DAV}resourcetype/{_DAV}collection") is not None

    def read_bytes(self, path: str) -> bytes:
        resp = self._request("GET", path)
        if resp.status_code == 404:
            raise StorageNotFound(path)
        if resp.status_code != 200:
            raise StorageError(f"WebDAV GET {path} -> HTTP {resp.status_code}")
        return resp.content

    def write_bytes(self, path: str, data: bytes) -> None:
        parent = posixpath.dirname(clean_path(path))
        if parent:
            self.makedirs(parent)
        resp = self._request("PUT", path, data=data)
        if resp.status_code not in (200, 201, 204):
            raise StorageError(f"WebDAV PUT {path} -> HTTP {resp.status_code}")

    def listdir(self, path: str) -> List[str]:
        tree = self._propfind(path, 1)
        if tree is None:
            return []
        self_path = unquote(urlparse(self._url(path)).path).rstrip("/")
        names = []
        for resp in tree.findall(f"{_DAV}response"):
            href = unquote(urlparse(resp.findtext(f"{_DAV}href") or "").path).rstrip("/")
            if href == self_path or not href:
                continue
            names.append(posixpath.basename(href))
        return sorted(names)

    def makedirs(self, path: str) -> None:
        rel = clean_path(path)
        if not rel:
            return
        built = ""
        for part in rel.split("/"):
            built = f"{built}/{part}" if built else part
            if built in self._known_dirs:
                continue
            resp = self._request("MKCOL", built)
            # 201 created; 405 already exists; 301/200 some servers answer for existing
            if resp.status_code not in (201, 405, 301, 200):
                raise StorageError(f"WebDAV MKCOL {built} -> HTTP {resp.status_code}")
            self._known_dirs.add(built)

    def delete(self, path: str) -> None:
        resp = self._request("DELETE", path)
        if resp.status_code not in (200, 202, 204, 404):
            raise StorageError(f"WebDAV DELETE {path} -> HTTP {resp.status_code}")
        prefix = clean_path(path)
        self._known_dirs = {d for d in self._known_dirs
                            if d != prefix and not d.startswith(prefix + "/")}

    def describe(self) -> str:
        return f"webdav:{self.base_url}"
