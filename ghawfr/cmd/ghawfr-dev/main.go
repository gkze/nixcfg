package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/gkze/ghawfr/devtools/codegenlockfile"
	"github.com/gkze/ghawfr/devtools/workflowschema"
)

func main() {
	ctx := context.Background()
	if err := run(ctx, os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "ghawfr-dev: %v\n", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string) error {
	if len(args) == 0 || isHelp(args[0]) {
		printUsage()
		return nil
	}
	switch args[0] {
	case "schema":
		return runSchema(ctx, args[1:])
	case "codegen":
		return runCodegen(ctx, args[1:])
	default:
		return fmt.Errorf("unknown command %q", strings.Join(args, " "))
	}
}

func runSchema(ctx context.Context, args []string) error {
	if len(args) == 0 || isHelp(args[0]) {
		printSchemaUsage()
		return nil
	}
	layout, err := workflowschema.DiscoverLayout()
	if err != nil {
		return err
	}
	client := &http.Client{}

	switch args[0] {
	case "fetch":
		includeDocs, err := parseSchemaFetchFlags(args[1:])
		if err != nil {
			return err
		}
		result, err := workflowschema.Fetch(ctx, layout, client, workflowschema.FetchOptions{IncludeDocs: includeDocs})
		if err != nil {
			return err
		}
		fmt.Printf(
			"fetched %s (%s) version=%s definitions=%d\n",
			rel(layout.ModuleRoot, result.Path),
			result.SHA256,
			result.Version,
			result.DefinitionCount,
		)
		for _, document := range result.Documents {
			fmt.Printf("cached docs %s\n", rel(layout.ModuleRoot, document.HTMLPath))
		}
		return nil
	case "inspect":
		if len(args) != 1 {
			return fmt.Errorf("inspect does not accept arguments")
		}
		summary, err := workflowschema.Inspect(layout)
		if err != nil {
			return err
		}
		fmt.Printf("official version=%s definitions=%d\n", summary.Version, summary.DefinitionCount)
		fmt.Printf("strict root properties: %s\n", strings.Join(summary.StrictRootProperties, ", "))
		fmt.Printf("strict events (%d): %s\n", len(summary.StrictEventNames), strings.Join(summary.StrictEventNames, ", "))
		fmt.Printf(
			"features image_version=%t schedule.timezone=%t workflow_dispatch.inputs=%t pull_request.tags=%t pull_request_target.tags=%t\n",
			summary.HasImageVersionEvent,
			summary.HasScheduleTimezone,
			summary.HasWorkflowDispatchInputs,
			summary.PullRequestSupportsTags,
			summary.PullRequestTargetSupportsTags,
		)
		return nil
	case "update":
		if len(args) != 1 {
			return fmt.Errorf("update does not accept arguments")
		}
		result, err := workflowschema.Fetch(ctx, layout, client, workflowschema.FetchOptions{IncludeDocs: true})
		if err != nil {
			return err
		}
		fmt.Printf(
			"updated %s (%s) version=%s definitions=%d\n",
			rel(layout.ModuleRoot, result.Path),
			result.SHA256,
			result.Version,
			result.DefinitionCount,
		)
		for _, document := range result.Documents {
			fmt.Printf("cached docs %s\n", rel(layout.ModuleRoot, document.HTMLPath))
		}
		return nil
	default:
		return fmt.Errorf("unknown schema command %q", args[0])
	}
}

func parseSchemaFetchFlags(args []string) (bool, error) {
	includeDocs := false
	for _, arg := range args {
		switch arg {
		case "-d", "--docs":
			includeDocs = true
		default:
			return false, fmt.Errorf("unknown schema fetch flag %q", arg)
		}
	}
	return includeDocs, nil
}

func runCodegen(ctx context.Context, args []string) error {
	if len(args) == 0 || isHelp(args[0]) {
		printCodegenUsage()
		return nil
	}
	if args[0] != "lock" {
		return fmt.Errorf("unknown codegen command %q", args[0])
	}
	if len(args) < 2 {
		return fmt.Errorf("codegen lock requires a manifest path")
	}
	manifestPath := args[1]
	var outputPath string
	includeMetadata := false
	for index := 2; index < len(args); index++ {
		switch args[index] {
		case "-o", "--output":
			if index+1 >= len(args) {
				return fmt.Errorf("%s requires a value", args[index])
			}
			outputPath = args[index+1]
			index++
		case "-m", "--include-metadata":
			includeMetadata = true
		default:
			return fmt.Errorf("unknown codegen lock flag %q", args[index])
		}
	}
	layout, err := codegenlockfile.DiscoverLayout()
	if err != nil {
		return err
	}
	result, err := codegenlockfile.Write(
		ctx,
		layout,
		manifestPath,
		outputPath,
		codegenlockfile.BuildOptions{IncludeMetadata: includeMetadata},
	)
	if err != nil {
		return err
	}
	fmt.Printf("wrote %s (%s)\n", rel(layout.RepoRoot, result.Path), result.SHA256)
	return nil
}

func rel(root, path string) string {
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return path
	}
	return filepath.ToSlash(rel)
}

func isHelp(arg string) bool {
	return arg == "-h" || arg == "--help" || arg == "help"
}

func printUsage() {
	fmt.Println("ghawfr-dev <command>")
	fmt.Println()
	fmt.Println("Commands:")
	fmt.Println("  schema     Maintain vendored workflow schema artifacts")
	fmt.Println("  codegen    Materialize canonical codegen lockfiles")
}

func printSchemaUsage() {
	fmt.Println("ghawfr-dev schema <command>")
	fmt.Println()
	fmt.Println("Commands:")
	fmt.Println("  fetch [-d|--docs] Fetch the official workflow DSL snapshot")
	fmt.Println("  inspect          Summarize the checked-in workflow DSL snapshot")
	fmt.Println("  update           Refresh the workflow DSL snapshot and docs cache")
}

func printCodegenUsage() {
	fmt.Println("ghawfr-dev codegen lock <manifest> [flags]")
	fmt.Println()
	fmt.Println("Flags:")
	fmt.Println("  -o, --output             Write the lockfile to this path")
	fmt.Println("  -m, --include-metadata   Include informational timestamps and provenance metadata")
}
