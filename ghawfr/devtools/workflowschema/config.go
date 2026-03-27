package workflowschema

const (
	defaultWorkflowSchemaSourceURL      = "https://raw.githubusercontent.com/actions/languageservices/83de320ba99ee2bdbb14a2869462a8033714cd96/workflow-parser/src/workflow-v1.0.json"
	defaultWorkflowSchemaSourceCommit   = "83de320ba99ee2bdbb14a2869462a8033714cd96"
	defaultWorkflowSchemaSourceTag      = "release-v0.3.49"
	defaultWorkflowSchemaPackageName    = "@actions/workflow-parser"
	defaultWorkflowSchemaPackageVersion = "0.3.49"
)

const (
	docWorkflowSyntax = "workflow-syntax-for-github-actions"
	docWorkflowEvents = "events-that-trigger-workflows"
)

// DocumentSpec describes one GitHub docs page used for workflow schema review.
type DocumentSpec struct {
	Name string
	URL  string
}

// DefaultDocumentSpecs returns the curated GitHub docs pages used by the schema devtool.
func DefaultDocumentSpecs() []DocumentSpec {
	return []DocumentSpec{
		{
			Name: docWorkflowSyntax,
			URL:  "https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions",
		},
		{
			Name: docWorkflowEvents,
			URL:  "https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows",
		},
	}
}
