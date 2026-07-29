"""Chat interface: renders history and drives the agent graph per turn."""

import streamlit as st


def extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def render_chat():
    for msg in st.session_state.history:
        role = "user" if msg["role"] == "user" else "assistant"
        with st.chat_message(role):
            st.markdown(msg["content"])

    if st.session_state.get("pending_message"):
        msg = st.session_state.pending_message
        st.session_state.pending_message = None
        st.session_state.history.append({"role": "user", "content": msg})
        with st.chat_message("user"):
            st.markdown(msg)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                config = {"configurable": {"thread_id": st.session_state.thread_id}}
                result = st.session_state.graph.invoke(
                    {"messages": [{"role": "user", "content": msg}]},
                    config=config,
                )
                response_text = extract_text(result["messages"][-1].content)
                st.markdown(response_text)

        st.session_state.history.append({"role": "assistant", "content": response_text})
        st.rerun()

    user_input = st.chat_input("How's your day going? Any updates that affect your fitness?")

    if user_input:
        st.session_state.history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                config = {"configurable": {"thread_id": st.session_state.thread_id}}
                result = st.session_state.graph.invoke(
                    {"messages": [{"role": "user", "content": user_input}]},
                    config=config,
                )
                response_text = extract_text(result["messages"][-1].content)
                st.markdown(response_text)

        st.session_state.history.append({"role": "assistant", "content": response_text})
        st.rerun()