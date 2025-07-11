# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License
"""
Reference:
 - [graphrag](https://github.com/microsoft/graphrag)
"""

import logging
import json
import re
from typing import Callable
from dataclasses import dataclass
import networkx as nx
import pandas as pd
from graphrag.general import leiden
from graphrag.general.community_report_prompt import COMMUNITY_REPORT_PROMPT
from graphrag.general.extractor import Extractor
from graphrag.general.leiden import add_community_info2graph
from rag.llm.chat_model import Base as CompletionLLM
from graphrag.utils import perform_variable_replacements, dict_has_keys_with_types, chat_limiter
from rag.utils import num_tokens_from_string
import trio
import os

# Community-specific limiter for more conservative concurrent requests
community_limiter = trio.CapacityLimiter(int(os.environ.get('MAX_CONCURRENT_COMMUNITIES', 3)))


@dataclass
class CommunityReportsResult:
    """Community reports result class definition."""

    output: list[str]
    structured_output: list[dict]


class CommunityReportsExtractor(Extractor):
    """Community reports extractor class definition."""

    _extraction_prompt: str
    _output_formatter_prompt: str
    _max_report_length: int

    def __init__(
            self,
            llm_invoker: CompletionLLM,
            max_report_length: int | None = None,
    ):
        super().__init__(llm_invoker)
        """Init method definition."""
        self._llm = llm_invoker
        self._extraction_prompt = COMMUNITY_REPORT_PROMPT
        self._max_report_length = max_report_length or 1500

    async def __call__(self, graph: nx.Graph, callback: Callable | None = None):
        for node_degree in graph.degree:
            graph.nodes[str(node_degree[0])]["rank"] = int(node_degree[1])

        communities: dict[str, dict[str, list]] = leiden.run(graph, {})
        total = sum([len(comm.items()) for _, comm in communities.items()])
        res_str = []
        res_dict = []
        over, token_count = 0, 0
        async def extract_community_report(community):
            nonlocal res_str, res_dict, over, token_count
            cm_id, cm = community
            weight = cm["weight"]
            ents = cm["nodes"]
            if len(ents) < 2:
                return
            
            # Prepare data
            ent_list = [{"entity": ent, "description": graph.nodes[ent]["description"]} for ent in ents]
            ent_df = pd.DataFrame(ent_list)

            rela_list = []
            k = 0
            for i in range(0, len(ents)):
                if k >= 10000:
                    break
                for j in range(i + 1, len(ents)):
                    if k >= 10000:
                        break
                    edge = graph.get_edge_data(ents[i], ents[j])
                    if edge is None:
                        continue
                    rela_list.append({"source": ents[i], "target": ents[j], "description": edge["description"]})
                    k += 1
            rela_df = pd.DataFrame(rela_list)

            prompt_variables = {
                "entity_df": ent_df.to_csv(index_label="id"),
                "relation_df": rela_df.to_csv(index_label="id")
            }
            text = perform_variable_replacements(self._extraction_prompt, variables=prompt_variables)
            gen_conf = {"temperature": 0.3}
            
            # Enhanced retry logic for API errors
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    async with community_limiter:  # Use community-specific limiter
                        with trio.move_on_after(150) as cancel_scope:  # Increased timeout
                            response = await trio.to_thread.run_sync(self._chat, text, [{"role": "user", "content": "Output:"}], gen_conf)
                        
                        if cancel_scope.cancelled_caught:
                            if attempt < max_retries - 1:
                                logging.warning(f"Community {cm_id} timeout on attempt {attempt + 1}, retrying...")
                                await trio.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            else:
                                logging.warning(f"Community {cm_id} timeout after {max_retries} attempts, skipping...")
                                return
                    
                    # Successfully got response, break out of retry loop
                    break
                    
                except Exception as e:
                    error_str = str(e).lower()
                    
                    # Check for specific API errors
                    if any(err in error_str for err in ["auth_subrequest_error", "internal_error", "500", "502", "503", "504"]):
                        if attempt < max_retries - 1:
                            wait_time = min(5 * (2 ** attempt), 30)  # Exponential backoff, max 30s
                            logging.warning(f"Community {cm_id} API error on attempt {attempt + 1}: {str(e)[:100]}... Retrying in {wait_time}s")
                            await trio.sleep(wait_time)
                            continue
                        else:
                            logging.error(f"Community {cm_id} failed after {max_retries} attempts due to API errors: {str(e)[:100]}...")
                            return
                    elif "rate_limit" in error_str or "quota" in error_str:
                        if attempt < max_retries - 1:
                            wait_time = min(10 * (2 ** attempt), 60)  # Longer wait for rate limits
                            logging.warning(f"Community {cm_id} rate limit on attempt {attempt + 1}. Waiting {wait_time}s")
                            await trio.sleep(wait_time)
                            continue
                        else:
                            logging.error(f"Community {cm_id} failed due to rate limits after {max_retries} attempts")
                            return
                    else:
                        # Other errors, don't retry
                        logging.error(f"Community {cm_id} failed with non-retryable error: {str(e)[:100]}...")
                        return
            
            # Process response
            try:
                token_count += num_tokens_from_string(text + response)
                
                # Clean and parse JSON response
                response = re.sub(r"^[^\{]*", "", response)
                response = re.sub(r"[^\}]*$", "", response)
                response = re.sub(r"\{\{", "{", response)
                response = re.sub(r"\}\}", "}", response)
                logging.debug(f"Community {cm_id} response: {response[:200]}...")
                
                try:
                    response_dict = json.loads(response)
                except json.JSONDecodeError as e:
                    logging.error(f"Community {cm_id} JSON parse error: {e}")
                    logging.error(f"Response content: {response[:500]}...")
                    return
                
                # Validate required fields
                if not dict_has_keys_with_types(response_dict, [
                            ("title", str),
                            ("summary", str),
                            ("findings", list),
                            ("rating", (int, float)),
                            ("rating_explanation", str),
                        ]):
                    logging.error(f"Community {cm_id} missing required fields in response")
                    return
                
                # Successfully processed community
                response_dict["weight"] = weight
                response_dict["entities"] = ents
                add_community_info2graph(graph, ents, response_dict["title"])
                res_str.append(self._get_text_output(response_dict))
                res_dict.append(response_dict)
                over += 1
                
                if callback:
                    callback(msg=f"Communities: {over}/{total}, used tokens: {token_count}")
                    
            except Exception as e:
                logging.error(f"Community {cm_id} processing error: {str(e)[:100]}...")
                return

        st = trio.current_time()
        
        # Process communities in batches to avoid overwhelming the API
        batch_size = int(os.environ.get('COMMUNITY_BATCH_SIZE', 5))
        all_communities = []
        for level, comm in communities.items():
            logging.info(f"Level {level}: Community: {len(comm.keys())}")
            all_communities.extend(comm.items())
        
        # Process communities in batches
        for batch_start in range(0, len(all_communities), batch_size):
            batch_end = min(batch_start + batch_size, len(all_communities))
            current_batch = all_communities[batch_start:batch_end]
            
            logging.info(f"Processing community batch {batch_start//batch_size + 1}/{(len(all_communities) + batch_size - 1)//batch_size} ({len(current_batch)} communities)")
            
            async with trio.open_nursery() as nursery:
                for community in current_batch:
                    nursery.start_soon(extract_community_report, community)
            
            # Small delay between batches to be gentle on the API
            if batch_end < len(all_communities):
                await trio.sleep(1)
        
        if callback:
            callback(msg=f"Community reports done in {trio.current_time() - st:.2f}s, used tokens: {token_count}")

        return CommunityReportsResult(
            structured_output=res_dict,
            output=res_str,
        )

    def _get_text_output(self, parsed_output: dict) -> str:
        title = parsed_output.get("title", "Report")
        summary = parsed_output.get("summary", "")
        findings = parsed_output.get("findings", [])

        def finding_summary(finding: dict):
            if isinstance(finding, str):
                return finding
            return finding.get("summary")

        def finding_explanation(finding: dict):
            if isinstance(finding, str):
                return ""
            return finding.get("explanation")

        report_sections = "\n\n".join(
            f"## {finding_summary(f)}\n\n{finding_explanation(f)}" for f in findings
        )
        return f"# {title}\n\n{summary}\n\n{report_sections}"
