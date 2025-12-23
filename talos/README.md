<!--
SPDX-FileCopyrightText: 2025 Sofie <sofie+git@mailbox.org>

SPDX-License-Identifier: MIT
-->

# HOWTO Upgrade

## Upgrading Talos

```bash
cd talos/
# change talconfig.yaml
talhelper genconfig
talosctl apply-config --nodes nas --file clusterconfig/home-nas.yaml
grep factory clusterconfig/home-nas.yaml
talosctl upgrade -n nas --image ...
```

### Verifcation

```bash
talosctl version
talosctl get extensions -n nas
```
