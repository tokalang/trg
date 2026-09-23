# RC13 candidate SDK diagnostic repro

These files isolate the first non-cascading `Vec` diagnostic seen while
checking `trg` with the local, unpublished Toka 1.0.0-rc.13 candidate archive
(`sha256:dede18597b39a61217ba9ba790bcd99d6d3b71ca4684393097055a718a6ae82f`).
This archive failed prequalification and is not a release-qualified SDK.

With the archive extracted, set `SDK` to its top-level directory, then run:

```sh
"$SDK/bin/tokac" -I "$SDK/lib" --check-only validation/rc13_repros/vec_string_control.tk
"$SDK/bin/tokac" -I "$SDK/lib" --check-only validation/rc13_repros/vec_nested_vec_field.tk
```

The `Vec<string>` control passes. The `Vec<Item>` case, where `Item` owns a
`Vec<string>` field, fails at SDK `lib/std/vec.tk:35` with E04662
`ElementDependenciesUnproven` for `raw_take storage[tail]`. E0402 diagnostics
that follow are cascades from the failed `outcome` initializer. No unsafe
operation appears in the repro source.
