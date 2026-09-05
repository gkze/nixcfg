//! Read the compiler requirement with the same ZON grammar as the build tool.
const std = @import("std");

const Manifest = struct {
    minimum_zig_version: []const u8,
};

fn parseManifest(
    allocator: std.mem.Allocator,
    source: [:0]const u8,
    diagnostics: ?*std.zon.parse.Diagnostics,
) !Manifest {
    return std.zon.parse.fromSlice(Manifest, allocator, source, diagnostics, .{
        .ignore_unknown_fields = true,
    });
}

pub fn main() !void {
    const allocator = std.heap.page_allocator;
    const args = try std.process.argsAlloc(allocator);
    defer std.process.argsFree(allocator, args);
    if (args.len != 2) return error.ExpectedManifestPath;

    const source = try std.fs.cwd().readFileAllocOptions(
        allocator,
        args[1],
        1024 * 1024,
        null,
        .of(u8),
        0,
    );
    defer allocator.free(source);
    var diagnostics: std.zon.parse.Diagnostics = .{};
    defer diagnostics.deinit(allocator);
    const manifest = parseManifest(allocator, source, &diagnostics) catch |err| {
        if (err == error.ParseZon) std.debug.print("{f}", .{diagnostics});
        return err;
    };
    defer std.zon.parse.free(allocator, manifest);
    try std.fs.File.stdout().writeAll(manifest.minimum_zig_version);
    try std.fs.File.stdout().writeAll("\n");
}

test "accept formatting, comments, and unrelated manifest fields" {
    const sources = [_][:0]const u8{
        \\.{
        \\    .minimum_zig_version = "0.15.1", // minimum compiler
        \\    .dependencies = .{ .tool = .{ .minimum_zig_version = "9.0.0" } },
        \\}
        ,
        \\.{ .name = .neutils, .version = "0.7.2", .minimum_zig_version = "0.15.1" }
        ,
        \\.{ .@"minimum_zig_version" = "0.15.\x31", .paths = .{"src", "build.zig"} }
        ,
    };
    for (sources) |source| {
        const manifest = try parseManifest(std.testing.allocator, source, null);
        defer std.zon.parse.free(std.testing.allocator, manifest);
        try std.testing.expectEqualStrings("0.15.1", manifest.minimum_zig_version);
    }
}

test "reject invalid ZON and missing, duplicate, or incorrectly typed requirements" {
    const sources = [_][:0]const u8{
        \\.{ .minimum_zig_version = "0.15.1"
        ,
        \\.{ .dependencies = .{ .tool = .{ .minimum_zig_version = "0.15.1" } } }
        ,
        \\.{ .minimum_zig_version = "0.15.1", .minimum_zig_version = "0.16.0" }
        ,
        \\.{ .minimum_zig_version = 15 }
        ,
        \\.{ .minimum_zig_version = "0.15.1", .other = @import("unexpected.zig") }
        ,
    };
    for (sources) |source| {
        try std.testing.expectError(error.ParseZon, parseManifest(std.testing.allocator, source, null));
    }
}
