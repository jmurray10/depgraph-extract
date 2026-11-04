from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task


@CrewBase
class TripCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def planner(self) -> Agent:
        return Agent(config=self.agents_config["planner"])

    @agent
    def scout(self) -> Agent:
        return Agent(config=self.agents_config["scout"])

    @agent
    def compiler(self) -> Agent:
        return Agent(config=self.agents_config["compiler"])

    @task
    def plan_task(self) -> Task:
        return Task(config=self.tasks_config["plan_task"], agent=self.planner())

    @task
    def scout_task(self) -> Task:
        return Task(config=self.tasks_config["scout_task"], agent=self.scout())

    @task
    def compile_task(self) -> Task:
        return Task(config=self.tasks_config["compile_task"], agent=self.compiler())

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, process=Process.sequential)
