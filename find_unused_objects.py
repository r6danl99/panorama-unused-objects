#!/usr/bin/env python3
"""Find unused shared address objects and address groups in Panorama configs.

Reads one or more full Panorama XML configuration exports, one after another.
For each config it collects every shared address object and shared address
group, then searches the rest of that config for a reference to each name.
Objects with no reference are written to an output file as a list of tuples:

    ("panorama_hostname", "object_name", "address" | "address-group")

Usage:
    python find_unused_objects.py pano1.xml pano2.xml [-o unused_objects.txt]
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ADDRESS = "address"
ADDRESS_GROUP = "address-group"

# Children that identify an <entry> as an object definition.
ADDRESS_VALUE_TAGS = {"ip-netmask", "ip-range", "ip-wildcard", "fqdn"}
GROUP_VALUE_TAGS = {"static", "dynamic"}

# Parts of an address-group definition that never hold object references
# (tags and descriptions are free text; dynamic filters match on tags).
GROUP_NON_REFERENCE_TAGS = {"description", "tag", "dynamic"}


def panorama_name(root, fallback):
    """Return the Panorama hostname stored in the config."""
    hostname = root.findtext("./devices/entry/deviceconfig/system/hostname")
    if hostname and hostname.strip():
        return hostname.strip()
    print(
        f"  WARNING: no hostname found in config, using file name '{fallback}'",
        file=sys.stderr,
    )
    return fallback


def is_definition(entry, parent_tag):
    """True if `entry` defines an address object or address group."""
    if entry.tag != "entry":
        return False
    child_tags = {child.tag for child in entry}
    if parent_tag == ADDRESS:
        return bool(child_tags & ADDRESS_VALUE_TAGS)
    if parent_tag == ADDRESS_GROUP:
        return bool(child_tags & GROUP_VALUE_TAGS)
    return False


def collect_references(root):
    """Count every string in the config that could be an object reference.

    A reference is either the text of an element (e.g. <member>name</member>
    or <translated-address>name</translated-address>) or the name attribute of
    an <entry> (e.g. an interface IP that points at an address object).

    Object definitions themselves are not references, so these are skipped:
      * address definitions (shared and device-group): name and whole body
      * address-group definitions: name, description, tags and dynamic filter
        (static members ARE counted, since they reference other objects)
    """
    references = Counter()
    # Stack of (element, parent tag, tags to skip among this element's children)
    stack = [(root, None, frozenset())]
    while stack:
        elem, parent_tag, skip_children = stack.pop()

        child_skip = frozenset()
        if is_definition(elem, parent_tag):
            if parent_tag == ADDRESS:
                continue
            child_skip = GROUP_NON_REFERENCE_TAGS
        else:
            if elem.tag == "entry" and elem.get("name"):
                references[elem.get("name")] += 1
            if elem.text and elem.text.strip():
                references[elem.text.strip()] += 1

        for child in elem:
            if child.tag not in skip_children and child.tag not in child_skip:
                stack.append((child, elem.tag, child_skip))
    return references


def shared_object_names(root, kind):
    return [
        entry.get("name")
        for entry in root.findall(f"./shared/{kind}/entry")
        if entry.get("name")
    ]


def find_unused(xml_path):
    """Return [(panorama, object_name, kind), ...] for one config file."""
    root = ET.parse(xml_path).getroot()
    if root.tag != "config":
        raise ValueError(f"root element is <{root.tag}>, expected <config>")

    panorama = panorama_name(root, Path(xml_path).stem)
    references = collect_references(root)

    unused = []
    for kind in (ADDRESS, ADDRESS_GROUP):
        names = shared_object_names(root, kind)
        unused_names = [name for name in names if references[name] == 0]
        unused.extend((panorama, name, kind) for name in unused_names)
        print(
            f"  {panorama}: {len(unused_names)} of {len(names)} "
            f"shared {kind} objects unused"
        )
    return unused


def write_results(results, output_path):
    """Write the results as a Python literal list, one tuple per line."""
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write("[\n")
        for row in results:
            handle.write(f"    {row!r},\n")
        handle.write("]\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "configs", nargs="+", help="Panorama XML configuration file(s)"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="unused_objects.txt",
        help="output file (default: %(default)s)",
    )
    args = parser.parse_args()

    results = []
    failed = False
    for xml_path in args.configs:
        print(f"Processing {xml_path}")
        try:
            results.extend(find_unused(xml_path))
        except (OSError, ET.ParseError, ValueError) as exc:
            failed = True
            print(f"  ERROR: skipping {xml_path}: {exc}", file=sys.stderr)

    write_results(results, args.output)
    print(f"Wrote {len(results)} unused objects to {args.output}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
