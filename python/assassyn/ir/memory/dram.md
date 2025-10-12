# DRAM Module

## Summary

This module implements DRAM (Dynamic Random Access Memory) simulation for Assassyn's IR. Unlike SRAM, DRAM operates as an off-chip memory module that interacts with the on-chip pipeline through asynchronous request/response mechanisms. The module extends `MemoryBase` to provide DRAM-specific functionality using intrinsic functions for memory request handling, as described in the [intrinsics documentation](../expr/intrinsic.md).

## Exposed Interfaces

### `class DRAM`

DRAM memory module that extends MemoryBase for off-chip memory simulation.

It calls `send_read_request(self, re, addr)` and `send_write_request(self, we, addr, wdata)`.
