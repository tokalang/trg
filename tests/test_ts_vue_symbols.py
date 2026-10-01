#!/usr/bin/env python3
"""
Test suite for:
- TRG-001: TypeScript / Vue inline type import defense (no false nesting like PreviewMatch::PreviewSearchState)
- TRG-001: ASI single-line type alias defense (no swallowing subsequent interface/class/functions)
- TRG-002: Vue SFC, Svelte, and Astro support in `trg view <file> --symbol <name>`
"""

import os
import sys
import json
import tempfile
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TRG_BIN = os.environ.get("TRG_BIN")
if not TRG_BIN:
    candidate_debug = REPO_ROOT / "target" / "debug" / "trg"
    candidate_rel = REPO_ROOT / "target" / "trg"
    TRG_BIN = str(candidate_debug if candidate_debug.exists() else candidate_rel)


def run_trg(args, cwd=None):
    cmd = [TRG_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def test_ts_inline_type_import_defense():
    """TRG-001: import { type Foo, type Bar } should not be extracted as declarations or nested."""
    code = """
import {
  type PreviewMatch,
  type PreviewSearchState,
} from "./types";

export {
  type ExportedSpecifier,
};

type ActualType = {
  valid: boolean;
};
"""
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(code)
        f.flush()
        res = run_trg(["symbols", f.name])
        assert res.returncode == 0, f"trg symbols failed: {res.stderr}"
        stdout = res.stdout

        # Assert no PreviewMatch or PreviewSearchState
        assert "PreviewMatch" not in stdout, f"PreviewMatch should not be extracted from import: {stdout}"
        assert "PreviewSearchState" not in stdout, f"PreviewSearchState should not be extracted from import: {stdout}"
        assert "ExportedSpecifier" not in stdout, f"ExportedSpecifier should not be extracted: {stdout}"

        # Assert ActualType is extracted
        assert "ActualType" in stdout, f"ActualType should be extracted: {stdout}"


def test_ts_asi_type_alias_defense():
    """TRG-001: type X = Y without semicolon (ASI) should close on line 1, not swallowing next interface."""
    code = """type PreviewPdfPage = PDFPageProxy

interface PreviewPdfRenderTask {
  taskId: string
}

type PreviewPdfDocument = PDFDocumentProxy
"""
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(code)
        f.flush()

        # 1. Text tree check
        res = run_trg(["symbols", f.name])
        assert res.returncode == 0, f"trg symbols failed: {res.stderr}"
        stdout = res.stdout

        # Neither PreviewPdfRenderTask nor PreviewPdfDocument should be nested
        assert "PreviewPdfPage::PreviewPdfRenderTask" not in stdout, f"False nesting detected: {stdout}"
        assert "PreviewPdfRenderTask::PreviewPdfDocument" not in stdout, f"False nesting detected: {stdout}"

        # 2. JSON check for exact ranges and scopes
        res_json = run_trg(["symbols", f.name, "--json"])
        assert res_json.returncode == 0, f"trg symbols --json failed: {res_json.stderr}"
        symbols = json.loads(res_json.stdout)

        assert len(symbols) == 3, f"Expected 3 symbols, got {len(symbols)}: {symbols}"

        sym0 = symbols[0]
        assert sym0["name"] == "PreviewPdfPage"
        assert sym0["range"] == [1, 1], f"Expected [1, 1], got {sym0['range']}"
        assert sym0["scope"] == ""

        sym1 = symbols[1]
        assert sym1["name"] == "PreviewPdfRenderTask"
        assert sym1["range"] == [3, 5], f"Expected [3, 5], got {sym1['range']}"
        assert sym1["scope"] == "", f"PreviewPdfRenderTask should have empty scope, got: {sym1['scope']}"

        sym2 = symbols[2]
        assert sym2["name"] == "PreviewPdfDocument"
        assert sym2["range"] == [7, 7], f"Expected [7, 7], got {sym2['range']}"
        assert sym2["scope"] == ""


def test_ts_multiline_block_type():
    """TRG-001: type Foo = { ... } should correctly span multiple lines and close at }."""
    code = """type ComplexType = {
  a: number;
  b: string;
}

function processComplex(val: ComplexType) {
  return val.a;
}
"""
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(code)
        f.flush()
        res = run_trg(["symbols", f.name, "--json"])
        assert res.returncode == 0, f"trg symbols --json failed: {res.stderr}"
        symbols = json.loads(res.stdout)

        assert len(symbols) == 2, f"Expected 2 symbols, got {len(symbols)}: {symbols}"
        assert symbols[0]["name"] == "ComplexType"
        assert symbols[0]["range"] == [1, 4]

        assert symbols[1]["name"] == "processComplex"
        assert symbols[1]["scope"] == ""


def test_vue_sfc_symbol_view():
    """TRG-002: trg view <path>.vue --symbol <name> should succeed without unsupported syntax error."""
    code = """<template>
  <div class="drawer">
    <h1>{{ title }}</h1>
  </div>
</template>

<script setup lang="ts">
interface DrawerProps {
  title: string
  visible: boolean
}

function handleClose() {
  console.log("closing drawer")
}
</script>

<style scoped>
.drawer { padding: 16px; }
</style>
"""
    with tempfile.NamedTemporaryFile(suffix=".vue", mode="w", delete=False) as f:
        f.write(code)
        f.flush()

        # Test view interface symbol
        res1 = run_trg(["view", f.name, "--symbol", "DrawerProps"])
        assert res1.returncode == 0, f"trg view --symbol DrawerProps failed: {res1.stderr} / {res1.stdout}"
        assert "interface DrawerProps" in res1.stdout
        assert "visible: boolean" in res1.stdout
        assert "unsupported syntax" not in res1.stderr
        assert "unsupported syntax" not in res1.stdout

        # Test view function symbol
        res2 = run_trg(["view", f.name, "--symbol", "handleClose"])
        assert res2.returncode == 0, f"trg view --symbol handleClose failed: {res2.stderr} / {res2.stdout}"
        assert "function handleClose" in res2.stdout
        assert "closing drawer" in res2.stdout


def test_svelte_and_astro_symbol_view():
    """TRG-002: .svelte and .astro files should also be supported by symbol view."""
    svelte_code = """<script>
function incrementCount() {
  count += 1;
}
</script>
<button on:click={incrementCount}>Click</button>
"""
    with tempfile.NamedTemporaryFile(suffix=".svelte", mode="w", delete=False) as f:
        f.write(svelte_code)
        f.flush()
        res = run_trg(["view", f.name, "--symbol", "incrementCount"])
        assert res.returncode == 0, f"trg view on svelte failed: {res.stderr} / {res.stdout}"
        assert "function incrementCount" in res.stdout

    astro_code = """---
interface Props {
  name: string;
}
const { name } = Astro.props;
---
<div>{name}</div>
"""
    with tempfile.NamedTemporaryFile(suffix=".astro", mode="w", delete=False) as f:
        f.write(astro_code)
        f.flush()
        res = run_trg(["view", f.name, "--symbol", "Props"])
        assert res.returncode == 0, f"trg view on astro failed: {res.stderr} / {res.stdout}"
        assert "interface Props" in res.stdout


def main():
    print("Testing TS inline type import defense...")
    test_ts_inline_type_import_defense()
    print("  Passed.")

    print("Testing TS ASI single-line type alias defense...")
    test_ts_asi_type_alias_defense()
    print("  Passed.")

    print("Testing TS multiline block type...")
    test_ts_multiline_block_type()
    print("  Passed.")

    print("Testing Vue SFC symbol view...")
    test_vue_sfc_symbol_view()
    print("  Passed.")

    print("Testing Svelte & Astro symbol view...")
    test_svelte_and_astro_symbol_view()
    print("  Passed.")

    print("ALL TS/Vue symbol and view qualification tests passed!")


if __name__ == "__main__":
    main()
