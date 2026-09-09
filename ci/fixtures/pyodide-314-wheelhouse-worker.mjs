const EXPECTED_ABI = "pyemscripten_2026_0_wasm32";

const IMPORTS = {
    apispec: "apispec",
    attrs: "attrs",
    blake3: "blake3",
    cbor2: "cbor2",
    cffi: "cffi",
    cryptography: "cryptography",
    falcon: "falcon",
    hio: "hio",
    hjson: "hjson",
    "http-sfv": "http_sfv",
    jsonschema: "jsonschema",
    "jsonschema-specifications": "jsonschema_specifications",
    keri: "keri",
    mnemonic: "mnemonic",
    msgpack: "msgpack",
    multicommand: "multicommand",
    multidict: "multidict",
    "ordered-set": "ordered_set",
    packaging: "packaging",
    prettytable: "prettytable",
    pycparser: "pycparser",
    pyrsistent: "pyrsistent",
    pysodium: "pysodium",
    pyyaml: "yaml",
    qrcode: "qrcode",
    referencing: "referencing",
    "rpds-py": "rpds",
    semver: "semver",
    setuptools: "setuptools",
    six: "six",
    sortedcontainers: "sortedcontainers",
    "typing-extensions": "typing_extensions",
    wcwidth: "wcwidth",
    wheel: "wheel",
};

const PROBE = String.raw`
import base64
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
import sysconfig

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name
from packaging.version import Version

manifest = json.loads(wheelhouse_manifest_json)
expected_imports = json.loads(wheelhouse_imports_json)
expected = {}
for item in manifest["wheels"]:
    name = canonicalize_name(item["name"])
    if name in expected:
        raise AssertionError(f"duplicate expected distribution: {name}")
    expected[name] = item["version"]

installed = {}
installed_files = []
for distribution in importlib.metadata.distributions():
    name = canonicalize_name(distribution.metadata["Name"])
    if name in installed:
        raise AssertionError(f"duplicate installed distribution: {name}")
    files = sorted(str(path) for path in (distribution.files or ()))
    installed[name] = {
        "name": distribution.metadata["Name"],
        "version": distribution.version,
        "requires": list(distribution.requires or ()),
        "files": files,
    }
    installed_files.extend(files)

missing = sorted(set(expected) - set(installed))
extra = sorted(set(installed) - set(expected))
wrong = {
    name: {"expected": version, "actual": installed.get(name, {}).get("version")}
    for name, version in expected.items()
    if installed.get(name, {}).get("version") != version
}
if missing or extra or wrong:
    raise AssertionError(
        f"installed distribution mismatch: missing={missing}, extra={extra}, wrong={wrong}"
    )
if importlib.metadata.version("pysodium") != "0.7.18":
    raise AssertionError("pysodium metadata version mismatch")
if "pychloride" in installed:
    raise AssertionError("pychloride distribution is installed")

marker_environment = default_environment()
marker_environment.update({
    "python_version": "3.14",
    "python_full_version": "3.14.2",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "os_name": "posix",
    "sys_platform": "emscripten",
    "platform_machine": "wasm32",
    "platform_system": "Emscripten",
    "extra": "",
})
dependency_edges = []
exclusions = []
for owner in sorted(installed):
    for raw in installed[owner]["requires"]:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate(marker_environment):
            continue
        dependency = canonicalize_name(requirement.name)
        if dependency == "lmdb":
            exclusions.append({"owner": owner, "requirement": raw})
            continue
        if dependency not in installed:
            raise AssertionError(f"missing dependency edge {owner}: {raw}")
        if requirement.specifier and Version(installed[dependency]["version"]) not in requirement.specifier:
            raise AssertionError(f"dependency version mismatch {owner}: {raw}")
        dependency_edges.append({"owner": owner, "requirement": raw, "selected": dependency})
if not exclusions or {canonicalize_name(Requirement(item["requirement"]).name) for item in exclusions} != {"lmdb"}:
    raise AssertionError(f"unexpected exclusion set: {exclusions}")

os.environ["MSGPACK_PUREPYTHON"] = "1"
module_paths = {}
for distribution_name, module_name in sorted(expected_imports.items()):
    module = importlib.import_module(module_name)
    path = str(getattr(module, "__file__", ""))
    if not path or "/site-packages/" not in path or "/fortweb/" in path:
        raise AssertionError(f"module did not load from site-packages: {module_name}={path}")
    module_paths[distribution_name] = path

for browser_module in ("hio.base.webduring", "keri.db.webbasing", "keri.db.webdbing"):
    module = importlib.import_module(browser_module)
    path = str(getattr(module, "__file__", ""))
    if "/site-packages/" not in path or "/fortweb/" in path:
        raise AssertionError(f"browser module did not load from site-packages: {browser_module}={path}")
    module_paths[browser_module] = path

import blake3
blake3_digest = blake3.blake3(b"").hexdigest()
if blake3_digest != "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262":
    raise AssertionError("Blake3 vector mismatch")

if importlib.util.find_spec("msgpack._cmsgpack") is not None:
    raise AssertionError("MessagePack C extension is present")
import msgpack
messagepack_bytes = msgpack.packb({"a": 1}, use_bin_type=True)
if messagepack_bytes.hex() != "81a16101" or msgpack.unpackb(messagepack_bytes, raw=False) != {"a": 1}:
    raise AssertionError("MessagePack pure vector failed")
if msgpack.Packer.__module__ != "msgpack.fallback":
    raise AssertionError(f"MessagePack did not use fallback: {msgpack.Packer.__module__}")

import cbor2
cbor_bytes = cbor2.dumps({"b": 1, "a": 2}, canonical=True)
if cbor_bytes.hex() != "a2616102616201" or cbor2.loads(cbor_bytes) != {"a": 2, "b": 1}:
    raise AssertionError("canonical CBOR vector failed")

import pysodium
sodium_version = f"{pysodium.sodium_major}.{pysodium.sodium_minor}.{pysodium.sodium_patch}"
if sodium_version != "1.0.22":
    raise AssertionError(f"wrong libsodium version: {sodium_version}")
public_key, secret_key = pysodium.crypto_sign_keypair()
message = b"fortweb-pyodide-314"
signature = pysodium.crypto_sign_detached(message, secret_key)
pysodium.crypto_sign_verify_detached(signature, message, public_key)
sodium_rejected_modified = False
try:
    pysodium.crypto_sign_verify_detached(signature, message + b"!", public_key)
except Exception:
    sodium_rejected_modified = True
if not sodium_rejected_modified:
    raise AssertionError("libsodium accepted a modified message")

stretched = pysodium.crypto_pwhash(
    outlen=16,
    passwd="wheelhouse-password",
    salt=b"NHCtv3Actrddf8jC",
    opslimit=2,
    memlimit=67_108_864,
    alg=pysodium.crypto_pwhash_ALG_ARGON2ID13,
)
if len(stretched) != 16:
    raise AssertionError("Argon2id output length mismatch")

import cryptography
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends.openssl.backend import backend as openssl_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

curves = {
    "p256": (
        ec.SECP256R1(),
        "036b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296",
    ),
    "secp256k1": (
        ec.SECP256K1(),
        "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
    ),
}
curve_results = {}
for curve_name, (curve, expected_public) in curves.items():
    for _ in range(50):
        private_key = ec.derive_private_key(1, curve)
        public_key = private_key.public_key()
        compressed = public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.CompressedPoint,
        )
        if compressed.hex() != expected_public:
            raise AssertionError(f"{curve_name} compressed public key mismatch")
        signature = private_key.sign(message, ec.ECDSA(hashes.SHA256()))
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        rejected = False
        try:
            public_key.verify(signature, message + b"!", ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            rejected = True
        if not rejected:
            raise AssertionError(f"{curve_name} accepted a modified message")
    curve_results[curve_name] = {"iterations": 50, "compressed_public_key": expected_public}

if importlib.util.find_spec("lmdb") is not None or "lmdb" in sys.modules:
    raise AssertionError("native LMDB is available in the browser worker")

stale_tokens = ("0.29.3", "cp313", "pyodide_2025_0", "hio_web", "keri_web", "pychloride")
stale_files = sorted(path for path in installed_files if any(token in path for token in stale_tokens))
if stale_files:
    raise AssertionError(f"stale installed files: {stale_files}")

tags = [str(tag) for tag in list(sys_tags())[:20]]
if not any(tag.endswith("-pyemscripten_2026_0_wasm32") for tag in tags):
    raise AssertionError(f"2026 ABI tag is absent: {tags}")
if platform.python_version() != "3.14.2":
    raise AssertionError(f"wrong Python version: {platform.python_version()}")

report = {
    "ok": True,
    "python_version": platform.python_version(),
    "python_full": sys.version,
    "platform": sys.platform,
    "sysconfig_platform": sysconfig.get_platform(),
    "abi": "pyemscripten_2026_0_wasm32",
    "tags": tags,
    "installed_distributions": installed,
    "dependency_edges": dependency_edges,
    "exclusions": exclusions,
    "module_paths": module_paths,
    "blake3": {"empty_digest": blake3_digest},
    "msgpack": {"vector": messagepack_bytes.hex(), "implementation": msgpack.Packer.__module__},
    "cbor2": {"canonical_vector": cbor_bytes.hex()},
    "pysodium": {
        "distribution_version": importlib.metadata.version("pysodium"),
        "libsodium_version": sodium_version,
        "signature_verified": True,
        "modified_message_rejected": sodium_rejected_modified,
        "argon2id": {
            "outlen": 16,
            "password_type": "str",
            "salt": "NHCtv3Actrddf8jC",
            "opslimit": 2,
            "memlimit": 67_108_864,
            "output_sha256": __import__("hashlib").sha256(stretched).hexdigest(),
        },
    },
    "cryptography": {
        "version": cryptography.__version__,
        "openssl": openssl_backend.openssl_version_text(),
        "curves": curve_results,
    },
    "lmdb_absent": True,
    "stale_files": stale_files,
}
json.dumps(report, sort_keys=True)
`;

let started = false;

self.onmessage = async (event) => {
    if (started || event.data?.type !== "start") {
        self.postMessage({ ok: false, error: "worker accepts exactly one start request" });
        return;
    }
    started = true;
    try {
        const buildBase = new URL(event.data.buildBase, self.location.href);
        if (
            buildBase.origin !== self.location.origin
            || buildBase.pathname !== "/fortweb/_wheelhouse-test/build/"
            || buildBase.search
            || buildBase.hash
        ) {
            throw new Error(`invalid build base: ${buildBase.href}`);
        }
        if (!buildBase.pathname.endsWith("/")) {
            buildBase.pathname += "/";
        }
        console.log(`[wheelhouse] fetching manifest ${buildBase.href}`);
        const manifestResponse = await fetch(new URL("manifest.json", buildBase));
        if (!manifestResponse.ok) {
            throw new Error(`manifest HTTP ${manifestResponse.status}`);
        }
        const manifest = await manifestResponse.json();
        if (manifest.schema !== 1 || manifest.runtime?.abi !== EXPECTED_ABI) {
            throw new Error("invalid Wheelhouse manifest runtime contract");
        }
        const runtimeBase = new URL("runtime/", buildBase);
        console.log(`[wheelhouse] importing Pyodide ${manifest.runtime.pyodide}`);
        const { loadPyodide } = await import(new URL("pyodide.mjs", runtimeBase).href);
        const pyodide = await loadPyodide({ indexURL: runtimeBase.href });
        for (const filename of manifest.install_order) {
            const wheelUrl = new URL(`wheelhouse/${filename}`, buildBase).href;
            console.log(`[wheelhouse] loading ${filename}`);
            await pyodide.loadPackage(wheelUrl);
        }
        pyodide.globals.set("wheelhouse_manifest_json", JSON.stringify(manifest));
        pyodide.globals.set("wheelhouse_imports_json", JSON.stringify(IMPORTS));
        console.log("[wheelhouse] running Python probes");
        const report = JSON.parse(await pyodide.runPythonAsync(PROBE));
        self.postMessage({ ok: true, report });
    } catch (error) {
        console.error(error);
        self.postMessage({
            ok: false,
            error: error instanceof Error ? error.message : String(error),
            stack: error instanceof Error ? error.stack : "",
        });
    }
};
