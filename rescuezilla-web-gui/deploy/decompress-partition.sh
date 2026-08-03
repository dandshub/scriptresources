#!/usr/bin/env bash
# Decompress a Rescuezilla/Clonezilla partition image (partclone OR raw/dd) into
# a single full-size raw image file, with a progress bar and a free-space check.
#
# Useful when a partition needs to be handed to another tool that wants a plain
# image — e.g. a BitLocker "used-space-only" (Encrypt-On-Write) volume that no
# Linux tool can decrypt, which you mount on Windows with OSFMount instead.
#
# Usage:
#   decompress-partition.sh <image-dir> <partition> <output.raw>
# Example:
#   decompress-partition.sh \
#     /mnt/dsbh/casey/james/2026-08-01-1618-img-rescuezilla \
#     nvme0n1p3 \
#     /mnt/dsbh/casey/p3.raw
set -euo pipefail

IMG_DIR="${1:?usage: $0 <image-dir> <partition> <output.raw>}"
PART="${2:?partition name, e.g. nvme0n1p3}"
OUT="${3:?output raw file path}"

cd "$IMG_DIR"

# Collect the image chunks (partclone "-ptcl-img" or raw "-dd-img") in order.
shopt -s nullglob
CHUNKS=( "$PART".*-ptcl-img.* "$PART".*-dd-img.* )
if (( ${#CHUNKS[@]} == 0 )); then
  echo "error: no image chunks found for '$PART' in $IMG_DIR" >&2
  exit 1
fi
IFS=$'\n' CHUNKS=( $(printf '%s\n' "${CHUNKS[@]}" | sort) ); unset IFS

# Choose a decompressor from the first chunk's compressor extension.
case "${CHUNKS[0]}" in
  *.gz.*|*.gz)     DECOMP=(gzip -dc) ;;
  *.zst.*|*.zst)   DECOMP=(zstd -dc) ;;
  *.lz4.*|*.lz4)   DECOMP=(lz4 -dc) ;;
  *.xz.*|*.xz)     DECOMP=(xz -dc) ;;
  *.bz2.*|*.bz2)   DECOMP=(bzip2 -dc) ;;
  *.lzo.*|*.lzo)   DECOMP=(lzop -dc) ;;
  *) echo "error: unrecognised compressor in ${CHUNKS[0]}" >&2; exit 1 ;;
esac
if ! command -v "${DECOMP[0]}" >/dev/null; then
  echo "error: ${DECOMP[0]} not installed" >&2; exit 1
fi

COMPRESSED=$(du -ch "${CHUNKS[@]}" | tail -1 | cut -f1)
OUT_DIR="$(dirname "$OUT")"
AVAIL_GB=$(( $(df -Pk "$OUT_DIR" | awk 'NR==2{print $4}') / 1024 / 1024 ))

echo "Partition   : $PART"
echo "Chunks      : ${#CHUNKS[@]}  (${COMPRESSED} compressed)"
echo "Decompressor: ${DECOMP[*]}"
echo "Output      : $OUT"
echo "Free space  : ${AVAIL_GB} GB on $(df -P "$OUT_DIR" | awk 'NR==2{print $6}')"
echo
echo "NOTE: the decompressed volume can be MUCH larger than the compressed"
echo "      chunks (free space in the partition expands to zeros/patterns)."
read -r -p "Proceed? [y/N] " ans
[[ "$ans" == [yY] || "$ans" == [yY][eE][sS] ]] || { echo "aborted."; exit 1; }

echo "Decompressing..."
if command -v pv >/dev/null; then
  cat "${CHUNKS[@]}" | pv -cN compressed | "${DECOMP[@]}" | pv -cN decompressed > "$OUT"
else
  echo "(tip: 'sudo apt install pv' for a live progress bar)"
  cat "${CHUNKS[@]}" | "${DECOMP[@]}" > "$OUT"
fi

echo
echo "Done: $OUT"
echo "Size: $(du -h "$OUT" | cut -f1)  ($(stat -c%s "$OUT") bytes)"
