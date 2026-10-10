<!-- AI-hint: Derived reference documentation for the mios CLI verbs and helper backends, derived directly from mios.toml [verbs]. -->

# MiOS CLI Reference

This document is derived directly from `usr/share/mios/mios.toml`.

<!-- MIOS-GEN:verbs -->
| Verb | Surface | Description |
|---|---|---|
| `mios _defaults` |  |  |
| `mios a2a_delegate` |  |  |
| `mios agent_route` |  |  |
| `mios ai` | windows | Open Open WebUI (rich LLM interface) in your browser |
| `mios app_search` |  |  |
| `mios apps` |  |  |
| `mios assess` | dev_vm | Comprehensive system capability report |
| `mios build` | dev_vm | Open mios.html, save edits, then build the OCI image inside MiOS-DEV |
| `mios close_app` |  |  |
| `mios close_window` |  |  |
| `mios code_mode` |  |  |
| `mios coderun` |  |  |
| `mios config` | windows | Edit mios.toml in the HTML configurator (no build) |
| `mios container_restart` |  |  |
| `mios container_status` |  |  |
| `mios crawl` |  |  |
| `mios cu_act` |  |  |
| `mios cu_atspi_query` |  |  |
| `mios cu_click` |  |  |
| `mios cu_ground` |  |  |
| `mios cu_key` |  |  |
| `mios cu_key_combo` |  |  |
| `mios cu_screenshot` |  |  |
| `mios cu_type` |  |  |
| `mios cu_verify` |  |  |
| `mios cu_window_list` |  |  |
| `mios dash` | windows | Show the MiOS dashboard (framed banner + fastfetch) |
| `mios dev` | dev_vm | Enter the MiOS-DEV podman machine |
| `mios directory_lookup` |  |  |
| `mios discord_send` |  |  |
| `mios docgen_build` |  |  |
| `mios docgen_convert` |  |  |
| `mios document` |  |  |
| `mios everything_search` |  |  |
| `mios fetch_page` |  |  |
| `mios file_edit` |  |  |
| `mios find_file` |  |  |
| `mios flatpak_install` |  |  |
| `mios flatpak_list` |  |  |
| `mios flatpak_preflight` |  |  |
| `mios flatpak_search` |  |  |
| `mios flatpak_show` |  |  |
| `mios flatpak_uninstall` |  |  |
| `mios flatpak_upgrade` |  |  |
| `mios focus_window` |  |  |
| `mios fs_search` |  |  |
| `mios handoff` |  |  |
| `mios help` | windows | List every verb |
| `mios ingest` |  |  |
| `mios iommu` | dev_vm | Pretty-print hardware IOMMU topology |
| `mios knowledge_search` |  |  |
| `mios linux_input` |  |  |
| `mios list_dir` |  |  |
| `mios list_windows` |  |  |
| `mios maximize_window` |  |  |
| `mios memory` |  |  |
| `mios memory_append` |  |  |
| `mios memory_forget` |  |  |
| `mios memory_replace` |  |  |
| `mios memory_rollback` |  |  |
| `mios memory_update` |  |  |
| `mios minimize_window` |  |  |
| `mios mios_apps` |  |  |
| `mios mios_find` |  |  |
| `mios move_window` |  |  |
| `mios open_app` |  |  |
| `mios open_url` |  |  |
| `mios os_recipe` |  |  |
| `mios pc_click` |  |  |
| `mios pc_click_element` |  |  |
| `mios pc_find_element` |  |  |
| `mios pc_key` |  |  |
| `mios pc_list_elements` |  |  |
| `mios pc_screenshot` |  |  |
| `mios pc_type` |  |  |
| `mios pc_uia_set_value` |  |  |
| `mios pc_uia_tree` |  |  |
| `mios pkg` |  |  |
| `mios position_window` |  |  |
| `mios powershell_run` |  |  |
| `mios process_list` |  |  |
| `mios profile` | dev_vm | Interactive hardware/system profiler menu |
| `mios pull` | windows | Sync root overlay to origin/main |
| `mios recall` |  |  |
| `mios remember` |  |  |
| `mios resize_window` |  |  |
| `mios restore_window` |  |  |
| `mios run_code` |  |  |
| `mios schedule` |  |  |
| `mios screen_layout` |  |  |
| `mios search_store` |  |  |
| `mios service_restart` |  |  |
| `mios service_status` |  |  |
| `mios shell_session` |  |  |
| `mios summarize` |  |  |
| `mios summary` | dev_vm | Quick ASCII system overview |
| `mios switch_app_default` |  |  |
| `mios sync_to_root` |  |  |
| `mios sys_env` |  |  |
| `mios sys_env_refresh` |  |  |
| `mios system` |  |  |
| `mios system_logs` |  |  |
| `mios system_status` |  |  |
| `mios text_create` |  |  |
| `mios text_insert` |  |  |
| `mios text_str_replace` |  |  |
| `mios text_view` |  |  |
| `mios theme` | dev_vm | Sync bibata/GTK/Qt themes |
| `mios tool_search` |  |  |
| `mios tune` | dev_vm | System-wide CPU isolation & latency tuning |
| `mios update` | all | Check and perform OS updates via bootc |
| `mios user` | dev_vm | Initialize user space (dotfiles/XDG) |
| `mios vault` |  |  |
| `mios verify_launch` |  |  |
| `mios vfio` | dev_vm | Configure GPU/USB passthrough (Isolation) |
| `mios viking_cat` |  |  |
| `mios viking_find` |  |  |
| `mios viking_ls` |  |  |
| `mios virt` | dev_vm | Apply optimized VM config + CPU pinning |
| `mios web_extract` |  |  |
| `mios web_scrape` |  |  |
| `mios web_search` |  |  |
| `mios window_op` |  |  |
| `mios window_state` |  |  |
| `mios windows_input` |  |  |
| `mios winget_install` |  |  |
| `mios winget_list` |  |  |
| `mios winget_search` |  |  |
| `mios winget_show` |  |  |
| `mios winget_uninstall` |  |  |
| `mios winget_upgrade` |  |  |
| `mios xbox` | dev_vm | Xbox VM Secure Boot / XML repair |

<!-- derived from usr/share/mios/mios.toml [verbs] (132 verb(s)) -->
<!-- /MIOS-GEN:verbs -->
