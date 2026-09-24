"""Task graph orchestration."""



# Object to store the entire profiling task graph, including the nodes, edges, and the task execution plan.
# And the execution stages
# Interface to the Django Database to store the task graph information, and the execution results.


# Use the graph library?



class TaskExcutionGraph:
    """Class to orchestrate the task graph for profiling."""

    def __init__(self, graph: dict):
        self.graph = graph

    

