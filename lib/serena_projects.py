#!/usr/bin/env python3
"""Refresh language-server selections for Serena's registered projects."""
from pathlib import Path

from serena.config.serena_config import ProjectConfig, SerenaConfig
from serena.util.yaml import load_yaml, save_yaml


def refresh(project_root, serena_config):
    project_root = Path(project_root)
    project_yml = serena_config.get_project_yml_location(project_root)
    detected = ProjectConfig.autogenerate(project_root, serena_config,
                                           save_to_disk=False, interactive=False)
    languages = [language.value for language in detected.language_servers]
    config = load_yaml(project_yml)
    config['language_servers'] = languages
    save_yaml(project_yml, config)
    local_yml = Path(project_yml).with_name(ProjectConfig.SERENA_LOCAL_PROJECT_FILE)
    if local_yml.exists():
        local = load_yaml(str(local_yml))
        if 'language_servers' in local:
            del local['language_servers']
            save_yaml(str(local_yml), local)
    print(f'Refreshed Serena languages for {project_root}: {", ".join(languages) or "none"}')


def main():
    serena_config = SerenaConfig.from_config_file()
    failures = 0
    for project in serena_config.projects:
        try:
            refresh(project.project_root, serena_config)
        except Exception as exc:
            failures += 1
            print(f'FAIL Serena language refresh for {project.project_root}: {type(exc).__name__}: {exc}')
    return failures > 0


if __name__ == '__main__':
    raise SystemExit(main())
