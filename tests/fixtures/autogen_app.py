"""Minimal AutoGen fixture used for parser tests."""

import autogen


user_proxy = autogen.UserProxyAgent(name="user_proxy", human_input_mode="NEVER")
planner = autogen.AssistantAgent(name="planner", llm_config={})
coder = autogen.AssistantAgent(name="coder", llm_config={})
reviewer = autogen.AssistantAgent(name="reviewer", llm_config={})


groupchat = autogen.GroupChat(
    agents=[planner, coder, reviewer],
    messages=[],
    max_round=10,
)

manager = autogen.GroupChatManager(groupchat=groupchat, llm_config={})


# direct chat
user_proxy.initiate_chat(manager, message="build something")
