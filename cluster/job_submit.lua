-- /etc/slurm/job_submit.lua
-- Hold all jobs submitted to the "multicats" partition until
-- MultiCATS assigns a start time via scontrol update BeginTime.

local FAR_FUTURE = 365 * 24 * 60 * 60  -- 1 year

function slurm_job_submit(job_desc, part_list, submit_uid)
    if job_desc.partition == "multicats" then
        if job_desc.begin_time == 0 or job_desc.begin_time == slurm.NO_VAL then
            job_desc.begin_time = os.time() + FAR_FUTURE
            slurm.log_info("multicats: holding job in partition %s", job_desc.partition)
        end
    end
    return slurm.SUCCESS
end

function slurm_job_modify(job_desc, job_rec, part_list, modify_uid)
    return slurm.SUCCESS
end

return slurm.SUCCESS
