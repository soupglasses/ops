<!--
SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>

SPDX-License-Identifier: 0BSD
-->

# Minecraft operations

The server pauses world ticking after 60 seconds without players. It keeps the world
loaded and accepts connections, so pausing reduces CPU usage without releasing all
memory. Backup quiescing separately flushes the world and briefly disables automatic
saving while VolSync takes its snapshot.

Java starts with a 1 GiB heap and may grow to 4 GiB. G1 checks for an idle collection
every five minutes so unused heap can return to the operating system. Live world and
mod objects remain resident, and container memory also includes native allocations
outside the Java heap. Equal initial and maximum heap sizes prevent this shrinking.

Use the application dashboard to compare memory working set before and after players
leave. Allow a full five-minute collection interval after pausing. A lower initial
heap does not guarantee a fixed idle footprint.

References: [container JVM settings](https://docker-minecraft-server.readthedocs.io/en/latest/configuration/jvm-options/)
and [G1 idle collection](https://docs.oracle.com/en/java/javase/25/gctuning/garbage-first-g1-garbage-collector1.html).
