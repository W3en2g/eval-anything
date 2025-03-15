"""
(t2t)支持vllm推理
"""

from typing import Any, Dict, List
from vllm import LLM, SamplingParams
from vllm.utils import cuda_device_count_stateless
from transformers import AutoTokenizer
from eval_anything.utils.data_type import InferenceInput, InferenceOutput
from eval_anything.utils.register import TemplateRegistry
from eval_anything.models.base_model import BaseModel
import os

class vllmLM(BaseModel):
    def __init__(self, model_cfgs: Dict[str, Any], infer_cfgs, **kwargs):
        self.model_cfgs = model_cfgs
        self.infer_cfgs = infer_cfgs
        self.sp_n = self.infer_cfgs.num_output
        self.sp_top_k = self.infer_cfgs.top_k
        self.sp_top_p = self.infer_cfgs.top_p
        self.sp_temperature = self.infer_cfgs.temperature
        self.sp_max_tokens = self.infer_cfgs.model_max_length
        self.sp_prompt_logprobs = self.infer_cfgs.prompt_logprobs
        self.sp_logprobs = self.infer_cfgs.logprobs

        self.llm_trust_remote_code = self.infer_cfgs.trust_remote_code
        self.llm_gpu_memory_utilization = self.infer_cfgs.gpu_utilization
        tensor_ps = self.infer_cfgs.num_gpu
        self.llm_tensor_parallel_size = tensor_ps if tensor_ps else cuda_device_count_stateless()

        self.model_id = self.model_cfgs.model_id
        self.model_name_or_path = self.model_cfgs.model_name_or_path
        self.chat_template = self.model_cfgs.chat_template
        self.template = TemplateRegistry.get_template(self.chat_template) if self.chat_template else None

        self.task2details = {}
        self.detailed_filename = f'{self.model_id}_detailed'
        self.brief_filename = f'{self.model_id}_brief'
        visible_devices = ','.join(str(i) for i in self.infer_cfgs.gpu_ids)
        os.environ["CUDA_VISIBLE_DEVICES"] = visible_devices
        self.init_model()

    def init_model(self) -> None:
        self.samplingparams = SamplingParams(
            n=self.sp_n,
            top_k=self.sp_top_k,
            top_p=self.sp_top_p,
            temperature=self.sp_temperature,
            max_tokens=self.sp_max_tokens,
            prompt_logprobs=self.sp_prompt_logprobs,
            logprobs=self.sp_logprobs,
        )

        self.model = LLM(
            model=self.model_name_or_path,
            tokenizer=self.model_name_or_path,
            trust_remote_code=self.llm_trust_remote_code,
            tensor_parallel_size=self.llm_tensor_parallel_size,
            gpu_memory_utilization=self.llm_gpu_memory_utilization,
            distributed_executor_backend="ray"
        )

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name_or_path)
        if self.tokenizer.pad_token is None:
            if hasattr(self.tokenizer, "add_special_tokens"):
                self.tokenizer.add_special_tokens({"pad_token": self.tokenizer.eos_token})
            else:
                print("Warning: tokenizer does not support adding special tokens")

    def _build_conversation_from_template(self, input_list: List[InferenceInput]) -> List[InferenceInput]:
        if self.template:
            for input in input_list:
                input.conversation = [
                    {"role": "system", "content": self.template.system_prompt},
                    *input.conversation,
                    {"role": "assistant", "content": self.template.assistant_prompt.format(output='')}
                ]
        return input_list

    def generation(self, inputs: Dict[str, List[InferenceInput]]) -> Dict[str, List[InferenceOutput]]:
        return self._generation(inputs)

    def _generation(self, input_list: List[InferenceInput]) -> Dict[str, List[InferenceOutput]]:
        input_list = self._build_conversation_from_template(input_list)
        
        prompts = self.tokenizer.apply_chat_template(
            [input.conversation for input in input_list],
            padding=True,
            add_generation_prompt=True,
            tokenize=False,
            return_tensors="pt"
        )

        outputs = self.model.generate(
            prompts=prompts, sampling_params=self.samplingparams
        )
        inference_outputs = [
            InferenceOutput.from_vllm_output(task=input.task, uuid=input.uuid, vllm_output=output, store_raw=True)
            for input, output in zip(input_list, outputs)
        ]

        return inference_outputs
    
    # TODO
    def shutdown_model(self):
        pass