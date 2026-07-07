"""Minimal CrewAI fixture used for parser tests."""

from crewai import Agent, Task, Crew


researcher = Agent(role="researcher", goal="find facts", backstory="...")
writer = Agent(role="writer", goal="draft", backstory="...")
editor = Agent(role="editor", goal="polish", backstory="...")
manager = Agent(role="manager", goal="coordinate", backstory="...")


research_task = Task(
    description="research the topic",
    agent=researcher,
)

draft_task = Task(
    description="draft an article",
    agent=writer,
    context=[research_task],
)

edit_task = Task(
    description="polish the draft",
    agent=editor,
    context=[draft_task],
)


crew = Crew(
    agents=[researcher, writer, editor],
    tasks=[research_task, draft_task, edit_task],
    manager_agent=manager,
)
