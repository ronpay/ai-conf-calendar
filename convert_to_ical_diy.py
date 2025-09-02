#!/usr/bin/env python3
"""
Conference deadline ICS generator with filtering by conference names
"""

import argparse
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from icalendar import Calendar, Event, Timezone, TimezoneStandard


def load_mapping(path: str = "conference/types.yml"):
    """Load category mapping from types.yml"""
    with open(path) as f:
        types = yaml.safe_load(f)
    SUB_MAPPING = {}
    for types_data in types:
        SUB_MAPPING[types_data["sub"]] = types_data["name"]
    return SUB_MAPPING


def get_timezone(tz_str: str) -> timezone:
    """Convert timezone string to datetime.timezone object"""
    if tz_str == "AoE":
        return timezone(timedelta(hours=-12))
    match = re.match(r"UTC([+-])(\d{1,2})$", tz_str)
    if not match:
        raise ValueError(f"Invalid timezone format: {tz_str}")
    sign, hours = match.groups()
    offset = int(hours) if sign == "+" else -int(hours)
    return timezone(timedelta(hours=offset))


def create_vtimezone(tz: timezone) -> Timezone:
    """Create VTIMEZONE component"""
    tz_offset = tz.utcoffset(datetime.now())
    offset_hours = tz_offset.total_seconds() // 3600
    tzid = f"UTC{offset_hours:+03.0f}:00"

    vtz = Timezone()
    vtz.add("TZID", tzid)

    std = TimezoneStandard()
    std.add("DTSTART", datetime(1970, 1, 1))
    std.add("TZOFFSETFROM", timedelta(hours=offset_hours))
    std.add("TZOFFSETTO", timedelta(hours=offset_hours))
    std.add("TZNAME", tzid)

    vtz.add_component(std)
    return vtz


def find_conference_files(conf_names: list[str], conference_dir: str = "conference"):
    """Find conference files matching the given conference names"""
    found_files = []
    conf_names_lower = [name.lower() for name in conf_names]

    # Walk through all conference directories
    for root, dirs, files in os.walk(conference_dir):
        # Skip types.yml
        if "types.yml" in files:
            files.remove("types.yml")

        for file in files:
            if file.endswith(".yml"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        conferences = yaml.safe_load(f)

                    # Check if any conference in this file matches our criteria
                    for conf_data in conferences:
                        title = conf_data.get("title", "").lower()
                        if any(conf_name in title for conf_name in conf_names_lower):
                            found_files.append(file_path)
                            break
                except Exception as e:
                    print(f"Warning: Could not read {file_path}: {e}")

    return found_files


def filter_conferences_by_name(file_paths: list[str], conf_names: list[str]):
    """Filter conferences by name from the given file paths"""
    filtered_data = []
    conf_names_lower = [name.lower() for name in conf_names]

    for file_path in file_paths:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                conferences = yaml.safe_load(f)

            for conf_data in conferences:
                title = conf_data.get("title", "").lower()
                if any(conf_name in title for conf_name in conf_names_lower):
                    filtered_data.append(conf_data)
        except Exception as e:
            print(f"Warning: Could not read {file_path}: {e}")

    return filtered_data


def convert_conferences_to_ical(
    conferences: list[dict], output_path: str, lang: str = "en", SUB_MAPPING: dict = {}
):
    """Convert filtered conferences to iCal format"""
    cal = Calendar()
    cal.add("prodid", "-//Conference Deadlines//ccfddl.com//")
    cal.add("version", "2.0")

    added_tzids = set()

    for conf_data in conferences:
        title = conf_data["title"]
        sub = conf_data["sub"]
        sub_chinese = SUB_MAPPING.get(sub, sub)
        rank = conf_data["rank"]
        dblp = conf_data["dblp"]

        for conf in conf_data["confs"]:
            year = conf["year"]
            link = conf["link"]
            timeline = conf["timeline"]
            timezone_str = conf["timezone"]
            place = conf["place"]
            date = conf["date"]

            for entry in timeline:
                deadline_list = []
                if "abstract_deadline" in entry:
                    deadline_list.append("abstract_deadline")
                if "deadline" in entry:
                    deadline_list.append("deadline")
                for deadline_key in deadline_list:
                    try:
                        tz = get_timezone(timezone_str)
                    except ValueError:
                        continue

                    # Add VTIMEZONE component
                    tz_offset = tz.utcoffset(datetime.now())
                    offset_hours = tz_offset.total_seconds() // 3600
                    tzid = f"UTC{offset_hours:+03.0f}:00"

                    if tzid not in added_tzids:
                        vtz = create_vtimezone(tz)
                        cal.add_component(vtz)
                        added_tzids.add(tzid)

                    # Determine deadline type
                    deadline_type, deadline_str = None, None
                    if deadline_key == "abstract_deadline":
                        deadline_type = ("摘要截稿", "Abstract Deadline")
                        deadline_str = entry["abstract_deadline"]
                    elif deadline_key == "deadline":
                        deadline_type = ("截稿日期", "Deadline")
                        deadline_str = entry["deadline"]
                    else:
                        continue  # Skip invalid entries

                    if deadline_str == "TBD":
                        continue  # Ignore TBD dates

                    # Parse date and time
                    is_all_day = False
                    try:
                        deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        try:
                            deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d")
                            is_all_day = True
                        except ValueError:
                            continue  # Invalid date format

                    # Create event object
                    event = Event()
                    event.add("uid", uuid.uuid4())
                    event.add("dtstamp", datetime.now(tz))

                    # Handle time fields
                    if is_all_day:
                        event.add("dtstart", deadline_dt.date())
                        event.add("dtend", (deadline_dt + timedelta(days=1)).date())
                    else:
                        aware_dt = deadline_dt.replace(tzinfo=tz)
                        event.add("dtstart", aware_dt)
                        event.add("dtend", aware_dt + timedelta(minutes=1))

                    # Build summary
                    if lang == "en":
                        summary = f"{title} {year} {deadline_type[1]}"
                    else:
                        summary = f"{title} {year} {deadline_type[0]}"

                    # Add comment if available
                    if "comment" in entry:
                        summary += f" [{entry['comment']}]"
                    event.add("summary", summary)

                    # Build detailed description
                    level_desc = [
                        f"CCF {rank['ccf']}" if rank["ccf"] != "N" else None,
                        f"CORE {rank['core']}" if rank.get("core", "N") != "N" else None,
                        f"THCPL {rank['thcpl']}" if rank.get("thcpl", "N") != "N" else None,
                    ]
                    level_desc = [line for line in level_desc if line]
                    if len(level_desc) > 0:
                        level_desc = ", ".join(level_desc)
                    else:
                        level_desc = None

                    if lang == "en":
                        description = [
                            f"{conf_data['description']}",
                            f"🗓️ Date: {date}",
                            f"📍 Location: {place}",
                            f"⏰ Original Deadline ({timezone_str}): {deadline_str}",
                            f"Category: {sub_chinese} ({sub})",
                            level_desc,
                            f"Conference Website: {link}",
                            f"DBLP Index: https://dblp.org/db/conf/{dblp}",
                        ]
                    else:
                        description = [
                            f"{conf_data['description']}",
                            f"🗓️ 会议时间: {date}",
                            f"📍 会议地点: {place}",
                            f"⏰ 原始截止时间 ({timezone_str}): {deadline_str}",
                            f"分类: {sub_chinese} ({sub})",
                            level_desc,
                            f"会议官网: {link}",
                            f"DBLP索引: https://dblp.org/db/conf/{dblp}",
                        ]
                    description = [line for line in description if line]
                    event.add("description", "\n".join(description))

                    # Add other metadata
                    event.add("location", place)
                    event.add("url", link)

                    cal.add_component(event)

    # Write output file
    with open(output_path, "wb") as f:
        f.write(cal.to_ical())


def main():
    """Main function to handle command line arguments and generate ICS files"""
    parser = argparse.ArgumentParser(description="Generate ICS files for specified conferences")
    parser.add_argument(
        "--conf", nargs="+", required=True, help="List of conference names to filter (e.g., CVPR ICCV)"
    )
    parser.add_argument(
        "--lang", choices=["en", "zh"], default="en", help="Language for the output (default: en)"
    )
    parser.add_argument("--output", "-o", help="Output file path (default: deadlines_<conf_names>.ics)")
    parser.add_argument(
        "--conference-dir",
        default="conference",
        help="Directory containing conference YAML files (default: conference)",
    )

    args = parser.parse_args()

    args.conf = [c.strip() for conf in args.conf for c in re.split(r"[,]+", conf.strip()) if c]

    # Change to the root directory of the project
    script_dir = Path(__file__).parent
    root_dir = script_dir.parent.parent.parent
    os.chdir(root_dir)

    # Load category mapping
    try:
        SUB_MAPPING = load_mapping("conference/types.yml")
    except FileNotFoundError:
        print("Error: conference/types.yml not found. Please run from the project root directory.")
        sys.exit(1)

    # Find conference files
    file_paths = find_conference_files(args.conf, args.conference_dir)

    if not file_paths:
        print(f"No conference files found matching: {', '.join(args.conf)}")
        sys.exit(1)

    # Filter conferences by name
    filtered_conferences = filter_conferences_by_name(file_paths, args.conf)

    if not filtered_conferences:
        print(f"No conferences found matching: {', '.join(args.conf)}")
        sys.exit(1)

    # Generate output filename if not provided
    if args.output:
        output_path = args.output
    else:
        output_path = f"deadlines_diy_{args.lang}.ics"

    # Convert to ICS
    convert_conferences_to_ical(filtered_conferences, output_path, args.lang, SUB_MAPPING)

    print(f"Generated ICS file: {output_path}")
    print(f"Found {len(filtered_conferences)} conferences matching: {', '.join(args.conf)}")


if __name__ == "__main__":
    main()
