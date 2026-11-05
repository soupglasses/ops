<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
SPDX-License-Identifier: MIT
-->

# Satellite status page

Uptime Kuma runs on satellite and continues probing and serving the public page
when Kubernetes is unavailable. This namespace declares its configuration through
the separately packaged `soupglasses/provider-uptimekuma` Crossplane provider.

Declare HTTP and game checks with `HTTPMonitor` and `GameMonitor`. The game resource
uses Kuma's native GameDig probe. A unique application name lets the provider adopt
existing monitors after a Kubernetes rebuild; ambiguous names fail closed.

`PublicStatusPage` references local monitor resources by kind and name. Its
composition resolves their observed remote IDs and keeps page membership current
when a monitor is recreated. Pending or missing references preserve the last page
configuration. Keep the referenced monitors on the same local ProviderConfig.

Manage configuration and page membership in Git. Management policies omit `Delete`
to preserve remote monitors, history and the page if Kubernetes declarations are
removed. The runtime uses a separately managed service account with namespaced
permissions and access only to the named credential Secret.

The normal Kuma login is encrypted in the local SOPS Secret. Prometheus API keys
only authorize metrics. Provider configuration and HTTP authentication selectors
cannot cross the application namespace.

Application data remains on satellite: the MariaDB `uptime_kuma` database and
`/var/lib/uptime-kuma/data` uploads. Preserve both when rebuilding satellite.
Restoring Kubernetes declarations alone does not restore historical samples.
