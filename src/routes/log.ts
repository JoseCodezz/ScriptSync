import { Router, Request, Response } from "express";
import { getLog } from "../ans/log";

export const logRouter = Router();

// A log Viewing Tool for Presentation 
logRouter.get("/log", (_req: Request, res: Response) => {
  return res.status(200).json({ entries: getLog() });
});