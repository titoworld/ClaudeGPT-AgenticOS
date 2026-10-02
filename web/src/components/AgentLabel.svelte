<script lang="ts">
  import { AGENT_LABEL } from '../lib/format';
  import type { Agent } from '../lib/protocol';
  import AgentIcon from './AgentIcon.svelte';

  interface Props {
    agent: Agent;
    size?: number;
  }

  let { agent, size = 18 }: Props = $props();
</script>

<span class="agent-label {agent}">
  <AgentIcon {agent} {size} />
  <span class="name">{AGENT_LABEL[agent]}</span>
</span>

<style>
  .agent-label {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-weight: 650;
    letter-spacing: 0.01em;
  }

  .claude .name {
    color: #ffc2a3;
  }

  .chatgpt .name {
    color: #a6f2dc;
  }
</style>
