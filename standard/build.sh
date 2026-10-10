#!/usr/bin/env bash
set -euo pipefail

# Run from any directory; optionally put generated files outside the checkout.
source_dir=$(cd "$(dirname "$0")" && pwd)
output_dir=${1:-$source_dir}
mkdir -p "$output_dir"
for draft in draft-dulaunoy-rifp-00 draft-dulaunoy-rifp-auth-00; do
    mmark "$source_dir/$draft.md" > "$output_dir/$draft.xml"
    xml2rfc --text --html --path "$output_dir" "$output_dir/$draft.xml"
done
