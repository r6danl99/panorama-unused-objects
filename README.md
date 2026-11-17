# panorama-unused-objects

Find and delete unused **shared** address objects and address groups on Palo Alto Panorama.

## Setup

```bash
pip install -r requirements.txt
```

`find_unused_objects.py` uses only the standard library; `pan-python` is needed for `delete_unused_objects.py`.

## 1. Find unused objects

```bash
python find_unused_objects.py pano-east.xml pano-west.xml
```

Each full Panorama XML config is read in turn. Every shared address object and shared address group is looked up in the rest of that config (device groups, templates, rulebases, other groups, and so on). Objects with no reference are written to `unused_objects.txt` (change with `-o`):

```python
[
    ('pano-east', 'old-server', 'address'),
    ('pano-east', 'old-servers-grp', 'address-group'),
]
```

The first field is the Panorama hostname read from the config (`deviceconfig/system/hostname`); the file name is used if the config has none.

How "used" is decided:

- Any element text or `entry` name elsewhere in the config that equals the object name counts as a use. This is deliberately conservative: an unrelated item with the same name (for example a service) keeps the object off the list.
- An object's own definition, descriptions, tags, dynamic group filters, and device-group objects of the same name do not count as a use.
- An address that is only a member of an unused group still counts as used. After the group is deleted, export the config again and re-run to catch it.

## 2. Delete them

Review `unused_objects.txt` first and remove any line you want to keep.

```bash
export PANORAMA_API_KEY='...'
python delete_unused_objects.py --dry-run
python delete_unused_objects.py
```

For each Panorama in the file, the script deletes the listed objects (groups first, then addresses) and then **commits to Panorama**. Nothing is pushed to managed devices. It asks for confirmation before making changes; `--yes` skips the prompt.

Options:

| Option | Purpose |
| --- | --- |
| `--api-key` / `$PANORAMA_API_KEY` | API key used for every Panorama |
| `--username` | Log in with a username instead; password comes from `$PANORAMA_PASSWORD` or a prompt |
| `--host-map NAME=ADDRESS` | Connect to `ADDRESS` for the Panorama called `NAME` in the file, when the hostname does not resolve (repeatable) |
| `--insecure` | Skip TLS certificate verification (self-signed certificates) |
| `--dry-run` | List what would be deleted without contacting Panorama |
| `--commit-timeout` | Seconds to wait for each commit (default 1800) |

Panorama refuses to delete an object that is still referenced; those are reported as `FAILED` and the rest continue. The exit code is non-zero if any delete or commit failed.
