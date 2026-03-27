package planner

import (
	"errors"
	"fmt"
	"sort"
	"strings"

	"gonum.org/v1/gonum/graph"
	"gonum.org/v1/gonum/graph/simple"
	"gonum.org/v1/gonum/graph/topo"

	"github.com/gkze/ghawfr/workflow"
)

// MissingDependency reports one unresolved needs edge.
type MissingDependency struct {
	JobID      workflow.JobID
	Dependency workflow.JobID
}

// ValidationError reports structural planning problems.
type ValidationError struct {
	MissingDependencies []MissingDependency
	Cycles              []workflow.JobIDs
}

func (e *ValidationError) Error() string {
	parts := make([]string, 0, 2)
	if len(e.MissingDependencies) > 0 {
		messages := make([]string, 0, len(e.MissingDependencies))
		for _, missing := range e.MissingDependencies {
			messages = append(messages, fmt.Sprintf("%s needs missing job %s", missing.JobID, missing.Dependency))
		}
		parts = append(parts, strings.Join(messages, "; "))
	}
	if len(e.Cycles) > 0 {
		messages := make([]string, 0, len(e.Cycles))
		for _, cycle := range e.Cycles {
			messages = append(messages, cycle.Join(" -> "))
		}
		parts = append(parts, "job dependency cycle: "+strings.Join(messages, "; "))
	}
	if len(parts) == 0 {
		return "workflow planning failed"
	}
	return strings.Join(parts, "; ")
}

// Stage is one concurrently runnable wave of jobs.
type Stage struct {
	Index int
	Jobs  workflow.JobIDs
}

// Plan is the pure dependency plan for one workflow.
type Plan struct {
	Order  workflow.JobIDs
	Stages []Stage
}

// Build validates one workflow and derives a deterministic job order and stage plan.
func Build(definition *workflow.Workflow) (*Plan, error) {
	if definition == nil {
		return nil, fmt.Errorf("workflow is nil")
	}
	missing := findMissingDependencies(definition)
	if len(missing) > 0 {
		return nil, &ValidationError{MissingDependencies: missing}
	}

	graphData := newGraph(definition)
	sorted, err := topo.SortStabilized(graphData.graph, graphData.sortNodes)
	if err != nil {
		var unorderable topo.Unorderable
		if errors.As(err, &unorderable) {
			return nil, &ValidationError{Cycles: graphData.cycles(unorderable)}
		}
		return nil, fmt.Errorf("topological sort workflow jobs: %w", err)
	}

	order := make(workflow.JobIDs, 0, len(sorted))
	for _, node := range sorted {
		if node == nil {
			continue
		}
		order = append(order, graphData.jobID(node.ID()))
	}
	stages := buildStages(definition, order, graphData.orderIndex)
	return &Plan{Order: order, Stages: stages}, nil
}

// Ready returns jobs whose dependencies are satisfied by completed and which
// are not already present in executed.
func Ready(definition *workflow.Workflow, executed workflow.JobSet, completed workflow.JobSet) (workflow.JobIDs, error) {
	plan, err := Build(definition)
	if err != nil {
		return nil, err
	}
	ready := make(workflow.JobIDs, 0, len(plan.Order))
	for _, jobID := range plan.Order {
		if executed[jobID] {
			continue
		}
		job := definition.Jobs[jobID]
		if job == nil {
			continue
		}
		if dependenciesSatisfied(job.Needs, completed) {
			ready = append(ready, jobID)
		}
	}
	return ready, nil
}

type jobNode struct{ id int64 }

func (n jobNode) ID() int64 { return n.id }

type dependencyGraph struct {
	graph      *simple.DirectedGraph
	idsByJob   map[workflow.JobID]int64
	jobsByID   map[int64]workflow.JobID
	orderIndex map[workflow.JobID]int
}

func newGraph(definition *workflow.Workflow) dependencyGraph {
	graph := simple.NewDirectedGraph()
	idsByJob := make(map[workflow.JobID]int64, len(definition.Jobs))
	jobsByID := make(map[int64]workflow.JobID, len(definition.Jobs))
	orderIndex := make(map[workflow.JobID]int, len(definition.Jobs))

	jobIDs := orderedJobIDs(definition)
	for index, jobID := range jobIDs {
		nodeID := int64(index + 1)
		node := jobNode{id: nodeID}
		graph.AddNode(node)
		idsByJob[jobID] = nodeID
		jobsByID[nodeID] = jobID
		orderIndex[jobID] = index
	}

	for _, jobID := range jobIDs {
		job := definition.Jobs[jobID]
		if job == nil {
			continue
		}
		for _, dependency := range job.Needs {
			from := graph.Node(idsByJob[dependency])
			to := graph.Node(idsByJob[jobID])
			graph.SetEdge(graph.NewEdge(from, to))
		}
	}

	return dependencyGraph{
		graph:      graph,
		idsByJob:   idsByJob,
		jobsByID:   jobsByID,
		orderIndex: orderIndex,
	}
}

func (g dependencyGraph) jobID(id int64) workflow.JobID {
	return g.jobsByID[id]
}

func (g dependencyGraph) sortNodes(nodes []graph.Node) {
	sort.SliceStable(nodes, func(i, j int) bool {
		left := g.jobID(nodes[i].ID())
		right := g.jobID(nodes[j].ID())
		leftIndex, leftOK := g.orderIndex[left]
		rightIndex, rightOK := g.orderIndex[right]
		switch {
		case leftOK && rightOK && leftIndex != rightIndex:
			return leftIndex < rightIndex
		case leftOK && !rightOK:
			return true
		case !leftOK && rightOK:
			return false
		default:
			return left < right
		}
	})
}

func (g dependencyGraph) cycles(unorderable topo.Unorderable) []workflow.JobIDs {
	cycles := make([]workflow.JobIDs, 0, len(unorderable))
	for _, component := range unorderable {
		cycle := make(workflow.JobIDs, 0, len(component))
		for _, node := range component {
			if node == nil {
				continue
			}
			cycle = append(cycle, g.jobID(node.ID()))
		}
		if len(cycle) == 0 {
			continue
		}
		sort.SliceStable(cycle, func(i, j int) bool {
			return g.orderIndex[cycle[i]] < g.orderIndex[cycle[j]]
		})
		cycles = append(cycles, cycle)
	}
	return cycles
}

func orderedJobIDs(definition *workflow.Workflow) workflow.JobIDs {
	if len(definition.JobOrder) > 0 {
		return definition.JobOrder.Clone()
	}
	jobIDs := make(workflow.JobIDs, 0, len(definition.Jobs))
	for jobID := range definition.Jobs {
		jobIDs = append(jobIDs, jobID)
	}
	sort.Slice(jobIDs, func(i, j int) bool {
		return jobIDs[i] < jobIDs[j]
	})
	return jobIDs
}

func findMissingDependencies(definition *workflow.Workflow) []MissingDependency {
	missing := make([]MissingDependency, 0)
	for _, jobID := range orderedJobIDs(definition) {
		job := definition.Jobs[jobID]
		if job == nil {
			continue
		}
		for _, dependency := range job.Needs {
			if _, ok := definition.Jobs[dependency]; ok {
				continue
			}
			missing = append(missing, MissingDependency{JobID: jobID, Dependency: dependency})
		}
	}
	return missing
}

func buildStages(definition *workflow.Workflow, order workflow.JobIDs, orderIndex map[workflow.JobID]int) []Stage {
	depthByJob := make(map[workflow.JobID]int, len(order))
	stageBuckets := make(map[int]workflow.JobIDs)
	maxDepth := 0
	for _, jobID := range order {
		job := definition.Jobs[jobID]
		depth := 0
		if job != nil {
			for _, dependency := range job.Needs {
				candidate := depthByJob[dependency] + 1
				if candidate > depth {
					depth = candidate
				}
			}
		}
		depthByJob[jobID] = depth
		stageBuckets[depth] = append(stageBuckets[depth], jobID)
		if depth > maxDepth {
			maxDepth = depth
		}
	}

	stages := make([]Stage, 0, maxDepth+1)
	for index := 0; index <= maxDepth; index++ {
		jobs := stageBuckets[index].Clone()
		sort.SliceStable(jobs, func(i, j int) bool {
			leftIndex, leftOK := orderIndex[jobs[i]]
			rightIndex, rightOK := orderIndex[jobs[j]]
			switch {
			case leftOK && rightOK && leftIndex != rightIndex:
				return leftIndex < rightIndex
			case leftOK && !rightOK:
				return true
			case !leftOK && rightOK:
				return false
			default:
				return jobs[i] < jobs[j]
			}
		})
		stages = append(stages, Stage{Index: index, Jobs: jobs})
	}
	return stages
}

func dependenciesSatisfied(needs workflow.JobIDs, completed workflow.JobSet) bool {
	for _, dependency := range needs {
		if !completed[dependency] {
			return false
		}
	}
	return true
}
